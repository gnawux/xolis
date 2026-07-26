use std::time::Duration;

use async_trait::async_trait;
use percent_encoding::{AsciiSet, CONTROLS, utf8_percent_encode};
use reqwest::{Client, multipart};
use serde::Serialize;
use thiserror::Error;

use crate::domain::{ExecuteCommandRequest, ExecuteCommandResponse, FileEntry, Sandbox};

const SANDBOX_ID_HEADER: &str = "x-sandbox-id";
const SANDBOX_NAMESPACE_HEADER: &str = "x-sandbox-namespace";
const SANDBOX_PORT_HEADER: &str = "x-sandbox-port";
const PATH_VALUE_ENCODE_SET: &AsciiSet = &CONTROLS
    .add(b' ')
    .add(b'"')
    .add(b'#')
    .add(b'%')
    .add(b'/')
    .add(b'<')
    .add(b'>')
    .add(b'?')
    .add(b'`')
    .add(b'{')
    .add(b'}');

#[derive(Debug, Error)]
pub enum RuntimeError {
    #[error("sandbox is not ready for runtime operations")]
    NotReady,
    #[error("sandbox runtime request timed out")]
    Timeout,
    #[error("sandbox runtime rejected the request: {0}")]
    Rejected(String),
    #[error("sandbox runtime is unavailable: {0}")]
    Unavailable(String),
}

#[async_trait]
pub trait SandboxRuntime: Send + Sync {
    async fn execute(
        &self,
        sandbox: &Sandbox,
        request: &ExecuteCommandRequest,
    ) -> Result<ExecuteCommandResponse, RuntimeError>;
    async fn upload(
        &self,
        sandbox: &Sandbox,
        path: &str,
        contents: Vec<u8>,
    ) -> Result<(), RuntimeError>;
    async fn download(&self, sandbox: &Sandbox, path: &str) -> Result<Vec<u8>, RuntimeError>;
    async fn list(&self, sandbox: &Sandbox, path: &str) -> Result<Vec<FileEntry>, RuntimeError>;
}

#[derive(Clone)]
pub struct RouterRuntimeClient {
    client: Client,
    base_url: String,
    namespace: String,
    port: u16,
}

impl RouterRuntimeClient {
    pub fn new(
        base_url: impl Into<String>,
        namespace: impl Into<String>,
        port: u16,
        request_timeout: Duration,
    ) -> Result<Self, RuntimeError> {
        let client = Client::builder()
            .timeout(request_timeout)
            .build()
            .map_err(|error| RuntimeError::Unavailable(error.to_string()))?;
        Ok(Self {
            client,
            base_url: base_url.into().trim_end_matches('/').to_owned(),
            namespace: namespace.into(),
            port,
        })
    }

    fn request(
        &self,
        method: reqwest::Method,
        endpoint: &str,
        sandbox: &Sandbox,
    ) -> Result<reqwest::RequestBuilder, RuntimeError> {
        let runtime_id = sandbox
            .runtime_id
            .as_deref()
            .ok_or(RuntimeError::NotReady)?;
        Ok(self
            .client
            .request(method, format!("{}/{endpoint}", self.base_url))
            .header(SANDBOX_ID_HEADER, runtime_id)
            .header(SANDBOX_NAMESPACE_HEADER, &self.namespace)
            .header(SANDBOX_PORT_HEADER, self.port))
    }

    async fn response(request: reqwest::RequestBuilder) -> Result<reqwest::Response, RuntimeError> {
        let response = request.send().await.map_err(map_reqwest_error)?;
        if response.status().is_success() {
            return Ok(response);
        }
        let status = response.status();
        let detail = response.text().await.unwrap_or_default();
        Err(RuntimeError::Rejected(format!("{status}: {detail}")))
    }
}

#[derive(Serialize)]
struct RuntimeExecuteRequest<'a> {
    command: &'a str,
    timeout_seconds: u64,
}

#[async_trait]
impl SandboxRuntime for RouterRuntimeClient {
    async fn execute(
        &self,
        sandbox: &Sandbox,
        request: &ExecuteCommandRequest,
    ) -> Result<ExecuteCommandResponse, RuntimeError> {
        let request = self
            .request(reqwest::Method::POST, "execute", sandbox)?
            .json(&RuntimeExecuteRequest {
                command: &request.command,
                timeout_seconds: request.timeout_seconds,
            });
        Self::response(request)
            .await?
            .json()
            .await
            .map_err(map_reqwest_error)
    }

    async fn upload(
        &self,
        sandbox: &Sandbox,
        path: &str,
        contents: Vec<u8>,
    ) -> Result<(), RuntimeError> {
        let part = multipart::Part::bytes(contents).file_name(path.to_owned());
        let request = self
            .request(reqwest::Method::POST, "upload", sandbox)?
            .multipart(multipart::Form::new().part("file", part));
        Self::response(request).await?;
        Ok(())
    }

    async fn download(&self, sandbox: &Sandbox, path: &str) -> Result<Vec<u8>, RuntimeError> {
        let path = encoded_path(path);
        let request = self.request(reqwest::Method::GET, &format!("download/{path}"), sandbox)?;
        Ok(Self::response(request)
            .await?
            .bytes()
            .await
            .map_err(map_reqwest_error)?
            .to_vec())
    }

    async fn list(&self, sandbox: &Sandbox, path: &str) -> Result<Vec<FileEntry>, RuntimeError> {
        let path = encoded_path(path);
        let request = self.request(reqwest::Method::GET, &format!("list/{path}"), sandbox)?;
        Self::response(request)
            .await?
            .json()
            .await
            .map_err(map_reqwest_error)
    }
}

fn encoded_path(path: &str) -> String {
    utf8_percent_encode(path.trim_start_matches('/'), PATH_VALUE_ENCODE_SET).to_string()
}

fn map_reqwest_error(error: reqwest::Error) -> RuntimeError {
    if error.is_timeout() {
        RuntimeError::Timeout
    } else {
        RuntimeError::Unavailable(error.to_string())
    }
}

#[cfg(test)]
mod tests {
    use std::{collections::BTreeMap, time::Duration};

    use chrono::Utc;
    use serde_json::json;
    use wiremock::{
        Mock, MockServer, ResponseTemplate,
        matchers::{body_json, body_string_contains, header, method, path},
    };

    use super::{RouterRuntimeClient, SandboxRuntime};
    use crate::domain::{ExecuteCommandRequest, Sandbox, SandboxState};

    fn sandbox() -> Sandbox {
        Sandbox {
            id: "xolis-public-id".to_owned(),
            tenant_id: "tenant-a".to_owned(),
            runtime_id: Some("xolis-runtime-id".to_owned()),
            profile: "python-basic-v1".to_owned(),
            state: SandboxState::Running,
            created_at: Utc::now(),
            expires_at: Utc::now(),
            metadata: BTreeMap::new(),
            reason: None,
        }
    }

    async fn client(server: &MockServer) -> RouterRuntimeClient {
        RouterRuntimeClient::new(
            server.uri(),
            "xolis-sandboxes",
            8888,
            Duration::from_secs(2),
        )
        .expect("runtime client")
    }

    #[tokio::test]
    async fn execute_sends_router_identity_and_timeout() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/execute"))
            .and(header("x-sandbox-id", "xolis-runtime-id"))
            .and(header("x-sandbox-namespace", "xolis-sandboxes"))
            .and(header("x-sandbox-port", "8888"))
            .and(body_json(
                json!({"command": "python -V", "timeout_seconds": 10}),
            ))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({
                "stdout": "Python 3.14.0\n",
                "stderr": "",
                "exit_code": 0
            })))
            .expect(1)
            .mount(&server)
            .await;

        let response = client(&server)
            .await
            .execute(
                &sandbox(),
                &ExecuteCommandRequest {
                    command: "python -V".to_owned(),
                    timeout_seconds: 10,
                },
            )
            .await
            .expect("execute response");
        assert_eq!(response.exit_code, 0);
        assert_eq!(response.stdout, "Python 3.14.0\n");
    }

    #[tokio::test]
    async fn file_paths_are_encoded_and_responses_are_preserved() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/download/dir%2Fhello%20world.txt"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(b"hello"))
            .expect(1)
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path("/list/workspace"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!([{
                "name": "hello.txt",
                "size": 5,
                "type": "file",
                "mod_time": 1.0
            }])))
            .expect(1)
            .mount(&server)
            .await;

        let client = client(&server).await;
        assert_eq!(
            client
                .download(&sandbox(), "/dir/hello world.txt")
                .await
                .expect("download"),
            b"hello"
        );
        let entries = client
            .list(&sandbox(), "workspace")
            .await
            .expect("directory listing");
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "hello.txt");
        assert_eq!(entries[0].size, 5);
        assert_eq!(entries[0].entry_type, "file");
        assert_eq!(entries[0].mod_time, 1.0);
    }

    #[tokio::test]
    async fn upload_uses_multipart_with_the_requested_path() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/upload"))
            .and(header("x-sandbox-id", "xolis-runtime-id"))
            .and(body_string_contains("filename=\"project/hello.txt\""))
            .and(body_string_contains("hello from xolis"))
            .respond_with(ResponseTemplate::new(200))
            .expect(1)
            .mount(&server)
            .await;

        client(&server)
            .await
            .upload(
                &sandbox(),
                "project/hello.txt",
                b"hello from xolis".to_vec(),
            )
            .await
            .expect("upload");
    }
}
