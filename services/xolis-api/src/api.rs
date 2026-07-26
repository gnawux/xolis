use std::sync::Arc;

use axum::{
    Json, Router,
    body::Body,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post, put},
};
use serde::Deserialize;

use crate::{
    domain::{
        CreateSandboxCommand, CreateSandboxRequest, ErrorResponse, ExecuteCommandRequest, Sandbox,
        SandboxList, SandboxState,
    },
    runtime::{RuntimeError, SandboxRuntime},
    store::{SandboxStore, StoreError},
};

const TENANT_HEADER: &str = "x-xolis-tenant";
const IDEMPOTENCY_HEADER: &str = "idempotency-key";

#[derive(Clone, Debug)]
pub struct AppConfig {
    pub profile: String,
    pub minimum_ttl_seconds: u64,
    pub maximum_ttl_seconds: u64,
    pub maximum_command_timeout_seconds: u64,
    pub maximum_upload_bytes: usize,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            profile: "python-basic-v1".to_owned(),
            minimum_ttl_seconds: 60,
            maximum_ttl_seconds: 7_200,
            maximum_command_timeout_seconds: 300,
            maximum_upload_bytes: 10 * 1024 * 1024,
        }
    }
}

#[derive(Clone)]
pub struct AppState {
    pub store: Arc<dyn SandboxStore>,
    pub runtime: Arc<dyn SandboxRuntime>,
    pub config: AppConfig,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/healthz", get(health))
        .route("/v1/sandboxes", post(create_sandbox).get(list_sandboxes))
        .route(
            "/v1/sandboxes/{id}",
            get(get_sandbox).delete(delete_sandbox),
        )
        .route("/v1/sandboxes/{id}/commands", post(execute_command))
        .route(
            "/v1/sandboxes/{id}/files/{*path}",
            put(upload_file).get(get_file),
        )
        .with_state(state)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

async fn create_sandbox(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(request): Json<CreateSandboxRequest>,
) -> Result<(StatusCode, Json<Sandbox>), ApiError> {
    let tenant_id = required_header(&headers, TENANT_HEADER)?;
    validate_request(&state.config, &request)?;

    let command = CreateSandboxCommand {
        tenant_id,
        idempotency_key: optional_header(&headers, IDEMPOTENCY_HEADER)?,
        profile: request.profile,
        ttl_seconds: request.ttl_seconds,
        metadata: request.metadata,
    };
    let sandbox = state.store.create(command).await?;

    Ok((StatusCode::ACCEPTED, Json(sandbox)))
}

async fn list_sandboxes(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<SandboxList>, ApiError> {
    let tenant_id = required_header(&headers, TENANT_HEADER)?;
    let items = state.store.list(&tenant_id).await?;
    Ok(Json(SandboxList { items }))
}

async fn get_sandbox(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Sandbox>, ApiError> {
    let tenant_id = required_header(&headers, TENANT_HEADER)?;
    Ok(Json(state.store.get(&tenant_id, &id).await?))
}

async fn delete_sandbox(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    let tenant_id = required_header(&headers, TENANT_HEADER)?;
    state.store.delete(&tenant_id, &id).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn execute_command(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(request): Json<ExecuteCommandRequest>,
) -> Result<Json<crate::domain::ExecuteCommandResponse>, ApiError> {
    let tenant_id = required_header(&headers, TENANT_HEADER)?;
    if request.command.trim().is_empty() {
        return Err(ApiError::bad_request("command cannot be empty"));
    }
    if request.timeout_seconds == 0
        || request.timeout_seconds > state.config.maximum_command_timeout_seconds
    {
        return Err(ApiError::bad_request(format!(
            "timeoutSeconds must be between 1 and {}",
            state.config.maximum_command_timeout_seconds
        )));
    }
    let sandbox = running_sandbox(&state, &tenant_id, &id).await?;
    Ok(Json(state.runtime.execute(&sandbox, &request).await?))
}

async fn upload_file(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((id, path)): Path<(String, String)>,
    body: Body,
) -> Result<StatusCode, ApiError> {
    let tenant_id = required_header(&headers, TENANT_HEADER)?;
    validate_file_path(&path)?;
    let sandbox = running_sandbox(&state, &tenant_id, &id).await?;
    let contents = axum::body::to_bytes(body, state.config.maximum_upload_bytes)
        .await
        .map_err(|_| {
            ApiError::payload_too_large(format!(
                "file cannot exceed {} bytes",
                state.config.maximum_upload_bytes
            ))
        })?;
    state
        .runtime
        .upload(&sandbox, &path, contents.to_vec())
        .await?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Debug, Default, Deserialize)]
struct FileQuery {
    #[serde(default)]
    list: bool,
}

async fn get_file(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((id, path)): Path<(String, String)>,
    Query(query): Query<FileQuery>,
) -> Result<Response, ApiError> {
    let tenant_id = required_header(&headers, TENANT_HEADER)?;
    validate_file_path(&path)?;
    let sandbox = running_sandbox(&state, &tenant_id, &id).await?;
    if query.list {
        return Ok(Json(state.runtime.list(&sandbox, &path).await?).into_response());
    }
    let contents = state.runtime.download(&sandbox, &path).await?;
    Ok((
        [(header::CONTENT_TYPE, "application/octet-stream")],
        contents,
    )
        .into_response())
}

async fn running_sandbox(state: &AppState, tenant_id: &str, id: &str) -> Result<Sandbox, ApiError> {
    let sandbox = state.store.get(tenant_id, id).await?;
    if sandbox.state != SandboxState::Running || sandbox.runtime_id.is_none() {
        return Err(ApiError::conflict(
            "sandbox is not ready for runtime operations",
        ));
    }
    Ok(sandbox)
}

fn validate_file_path(path: &str) -> Result<(), ApiError> {
    if path.is_empty() || path.contains('\0') || path.split('/').any(|component| component == "..")
    {
        return Err(ApiError::bad_request(
            "file path must stay within /workspace",
        ));
    }
    Ok(())
}

fn validate_request(config: &AppConfig, request: &CreateSandboxRequest) -> Result<(), ApiError> {
    if request.profile != config.profile {
        return Err(ApiError::bad_request(format!(
            "profile must be {}",
            config.profile
        )));
    }
    if !(config.minimum_ttl_seconds..=config.maximum_ttl_seconds).contains(&request.ttl_seconds) {
        return Err(ApiError::bad_request(format!(
            "ttlSeconds must be between {} and {}",
            config.minimum_ttl_seconds, config.maximum_ttl_seconds
        )));
    }
    if request.metadata.len() > 16 {
        return Err(ApiError::bad_request(
            "metadata cannot contain more than 16 entries",
        ));
    }
    Ok(())
}

fn required_header(headers: &HeaderMap, name: &'static str) -> Result<String, ApiError> {
    optional_header(headers, name)?
        .ok_or_else(|| ApiError::unauthorized(format!("missing {name} header")))
}

fn optional_header(headers: &HeaderMap, name: &'static str) -> Result<Option<String>, ApiError> {
    headers
        .get(name)
        .map(|value| {
            value
                .to_str()
                .map(str::trim)
                .map(str::to_owned)
                .map_err(|_| ApiError::bad_request(format!("invalid {name} header")))
        })
        .transpose()
        .map(|value| value.filter(|value| !value.is_empty()))
}

#[derive(Debug)]
pub struct ApiError {
    status: StatusCode,
    code: &'static str,
    message: String,
}

impl ApiError {
    fn bad_request(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            code: "invalid_request",
            message: message.into(),
        }
    }

    fn unauthorized(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            code: "unauthorized",
            message: message.into(),
        }
    }

    fn conflict(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::CONFLICT,
            code: "conflict",
            message: message.into(),
        }
    }

    fn payload_too_large(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::PAYLOAD_TOO_LARGE,
            code: "payload_too_large",
            message: message.into(),
        }
    }
}

impl From<StoreError> for ApiError {
    fn from(error: StoreError) -> Self {
        match error {
            StoreError::NotFound => Self {
                status: StatusCode::NOT_FOUND,
                code: "not_found",
                message: error.to_string(),
            },
            StoreError::Conflict(_) => Self {
                status: StatusCode::CONFLICT,
                code: "conflict",
                message: error.to_string(),
            },
            StoreError::Unavailable(_) => Self {
                status: StatusCode::SERVICE_UNAVAILABLE,
                code: "unavailable",
                message: error.to_string(),
            },
        }
    }
}

impl From<RuntimeError> for ApiError {
    fn from(error: RuntimeError) -> Self {
        match error {
            RuntimeError::NotReady => Self::conflict(error.to_string()),
            RuntimeError::Timeout => Self {
                status: StatusCode::GATEWAY_TIMEOUT,
                code: "runtime_timeout",
                message: error.to_string(),
            },
            RuntimeError::Rejected(_) => Self {
                status: StatusCode::BAD_GATEWAY,
                code: "runtime_rejected",
                message: error.to_string(),
            },
            RuntimeError::Unavailable(_) => Self {
                status: StatusCode::SERVICE_UNAVAILABLE,
                code: "runtime_unavailable",
                message: error.to_string(),
            },
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(ErrorResponse {
                code: self.code,
                message: self.message,
            }),
        )
            .into_response()
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use async_trait::async_trait;
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use http_body_util::BodyExt;
    use serde_json::{Value, json};
    use tower::ServiceExt;

    use super::{AppConfig, AppState, router};
    use crate::{
        domain::{ExecuteCommandRequest, ExecuteCommandResponse, FileEntry, Sandbox},
        runtime::{RuntimeError, SandboxRuntime},
        store::InMemorySandboxStore,
    };

    #[derive(Default)]
    struct TestRuntime {
        uploads: Mutex<Vec<(String, Vec<u8>)>>,
    }

    #[async_trait]
    impl SandboxRuntime for TestRuntime {
        async fn execute(
            &self,
            _sandbox: &Sandbox,
            request: &ExecuteCommandRequest,
        ) -> Result<ExecuteCommandResponse, RuntimeError> {
            Ok(ExecuteCommandResponse {
                stdout: format!("ran: {}", request.command),
                stderr: String::new(),
                exit_code: 0,
            })
        }

        async fn upload(
            &self,
            _sandbox: &Sandbox,
            path: &str,
            contents: Vec<u8>,
        ) -> Result<(), RuntimeError> {
            self.uploads
                .lock()
                .expect("uploads lock")
                .push((path.to_owned(), contents));
            Ok(())
        }

        async fn download(&self, _sandbox: &Sandbox, path: &str) -> Result<Vec<u8>, RuntimeError> {
            Ok(format!("contents of {path}").into_bytes())
        }

        async fn list(
            &self,
            _sandbox: &Sandbox,
            _path: &str,
        ) -> Result<Vec<FileEntry>, RuntimeError> {
            Ok(vec![FileEntry {
                name: "hello.txt".to_owned(),
                size: 5,
                entry_type: "file".to_owned(),
                mod_time: 1.0,
            }])
        }
    }

    fn app() -> axum::Router {
        router(AppState {
            store: Arc::new(InMemorySandboxStore::default()),
            runtime: Arc::new(TestRuntime::default()),
            config: AppConfig::default(),
        })
    }

    fn app_with_store(config: AppConfig) -> (axum::Router, InMemorySandboxStore, Arc<TestRuntime>) {
        let store = InMemorySandboxStore::default();
        let runtime = Arc::new(TestRuntime::default());
        let application = router(AppState {
            store: Arc::new(store.clone()),
            runtime: runtime.clone(),
            config,
        });
        (application, store, runtime)
    }

    fn create_request(
        tenant: Option<&str>,
        idempotency_key: Option<&str>,
        ttl: u64,
    ) -> Request<Body> {
        let mut builder = Request::builder()
            .method("POST")
            .uri("/v1/sandboxes")
            .header("content-type", "application/json");
        if let Some(tenant) = tenant {
            builder = builder.header("x-xolis-tenant", tenant);
        }
        if let Some(key) = idempotency_key {
            builder = builder.header("idempotency-key", key);
        }
        builder
            .body(Body::from(
                json!({
                    "profile": "python-basic-v1",
                    "ttlSeconds": ttl,
                    "metadata": {"requestId": "test-001"}
                })
                .to_string(),
            ))
            .expect("request")
    }

    async fn json_body(response: axum::response::Response) -> Value {
        let bytes = response
            .into_body()
            .collect()
            .await
            .expect("response body")
            .to_bytes();
        serde_json::from_slice(&bytes).expect("JSON response")
    }

    #[tokio::test]
    async fn health_is_public() {
        let response = app()
            .oneshot(
                Request::get("/healthz")
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(json_body(response).await, json!({"status": "ok"}));
    }

    #[tokio::test]
    async fn tenant_header_is_required() {
        let response = app()
            .oneshot(create_request(None, None, 300))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn create_is_idempotent_and_tenant_scoped() {
        let application = app();
        let first = application
            .clone()
            .oneshot(create_request(Some("tenant-a"), Some("request-1"), 300))
            .await
            .expect("first response");
        assert_eq!(first.status(), StatusCode::ACCEPTED);
        let first_body = json_body(first).await;

        let second = application
            .clone()
            .oneshot(create_request(Some("tenant-a"), Some("request-1"), 300))
            .await
            .expect("second response");
        assert_eq!(second.status(), StatusCode::ACCEPTED);
        let second_body = json_body(second).await;
        assert_eq!(first_body["id"], second_body["id"]);

        let sandbox_id = first_body["id"].as_str().expect("sandbox id");
        let hidden = application
            .oneshot(
                Request::get(format!("/v1/sandboxes/{sandbox_id}"))
                    .header("x-xolis-tenant", "tenant-b")
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("response");
        assert_eq!(hidden.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn ttl_is_bounded() {
        let response = app()
            .oneshot(create_request(Some("tenant-a"), None, 30))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(json_body(response).await["code"], "invalid_request");
    }

    #[tokio::test]
    async fn delete_removes_the_sandbox() {
        let application = app();
        let created = application
            .clone()
            .oneshot(create_request(Some("tenant-a"), None, 300))
            .await
            .expect("create response");
        let sandbox_id = json_body(created).await["id"]
            .as_str()
            .expect("sandbox id")
            .to_owned();

        let deleted = application
            .clone()
            .oneshot(
                Request::delete(format!("/v1/sandboxes/{sandbox_id}"))
                    .header("x-xolis-tenant", "tenant-a")
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("delete response");
        assert_eq!(deleted.status(), StatusCode::NO_CONTENT);

        let missing = application
            .oneshot(
                Request::get(format!("/v1/sandboxes/{sandbox_id}"))
                    .header("x-xolis-tenant", "tenant-a")
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("get response");
        assert_eq!(missing.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn runtime_operations_are_tenant_scoped_and_bounded() {
        let (application, store, runtime) = app_with_store(AppConfig {
            maximum_upload_bytes: 5,
            ..AppConfig::default()
        });
        let created = application
            .clone()
            .oneshot(create_request(Some("tenant-a"), None, 300))
            .await
            .expect("create response");
        let sandbox_id = json_body(created).await["id"]
            .as_str()
            .expect("sandbox id")
            .to_owned();

        let pending = application
            .clone()
            .oneshot(
                Request::post(format!("/v1/sandboxes/{sandbox_id}/commands"))
                    .header("x-xolis-tenant", "tenant-a")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"command":"python -V"}"#))
                    .expect("request"),
            )
            .await
            .expect("pending response");
        assert_eq!(pending.status(), StatusCode::CONFLICT);

        store.mark_running(&sandbox_id).await;
        let command = application
            .clone()
            .oneshot(
                Request::post(format!("/v1/sandboxes/{sandbox_id}/commands"))
                    .header("x-xolis-tenant", "tenant-a")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"command":"python -V","timeoutSeconds":10}"#))
                    .expect("request"),
            )
            .await
            .expect("command response");
        assert_eq!(command.status(), StatusCode::OK);
        assert_eq!(json_body(command).await["stdout"], "ran: python -V");

        let upload = application
            .clone()
            .oneshot(
                Request::put(format!(
                    "/v1/sandboxes/{sandbox_id}/files/project/hello.txt"
                ))
                .header("x-xolis-tenant", "tenant-a")
                .body(Body::from("hello"))
                .expect("request"),
            )
            .await
            .expect("upload response");
        assert_eq!(upload.status(), StatusCode::NO_CONTENT);
        assert_eq!(
            runtime.uploads.lock().expect("uploads lock").as_slice(),
            &[("project/hello.txt".to_owned(), b"hello".to_vec())]
        );

        let oversized = application
            .clone()
            .oneshot(
                Request::put(format!(
                    "/v1/sandboxes/{sandbox_id}/files/project/large.txt"
                ))
                .header("x-xolis-tenant", "tenant-a")
                .body(Body::from("larger"))
                .expect("request"),
            )
            .await
            .expect("oversized response");
        assert_eq!(oversized.status(), StatusCode::PAYLOAD_TOO_LARGE);

        let listing = application
            .clone()
            .oneshot(
                Request::get(format!(
                    "/v1/sandboxes/{sandbox_id}/files/project?list=true"
                ))
                .header("x-xolis-tenant", "tenant-a")
                .body(Body::empty())
                .expect("request"),
            )
            .await
            .expect("list response");
        assert_eq!(listing.status(), StatusCode::OK);
        assert_eq!(json_body(listing).await[0]["name"], "hello.txt");

        let traversal = application
            .oneshot(
                Request::put(format!(
                    "/v1/sandboxes/{sandbox_id}/files/project/%2E%2E/secrets"
                ))
                .header("x-xolis-tenant", "tenant-a")
                .body(Body::empty())
                .expect("request"),
            )
            .await
            .expect("traversal response");
        assert_eq!(traversal.status(), StatusCode::BAD_REQUEST);
    }
}
