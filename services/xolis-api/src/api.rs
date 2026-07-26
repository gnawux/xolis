use std::sync::Arc;

use axum::{
    Json, Router,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
};

use crate::{
    domain::{CreateSandboxCommand, CreateSandboxRequest, ErrorResponse, Sandbox, SandboxList},
    store::{SandboxStore, StoreError},
};

const TENANT_HEADER: &str = "x-xolis-tenant";
const IDEMPOTENCY_HEADER: &str = "idempotency-key";

#[derive(Clone, Debug)]
pub struct AppConfig {
    pub profile: String,
    pub minimum_ttl_seconds: u64,
    pub maximum_ttl_seconds: u64,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            profile: "python-basic-v1".to_owned(),
            minimum_ttl_seconds: 60,
            maximum_ttl_seconds: 7_200,
        }
    }
}

#[derive(Clone)]
pub struct AppState {
    pub store: Arc<dyn SandboxStore>,
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
    use std::sync::Arc;

    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use http_body_util::BodyExt;
    use serde_json::{Value, json};
    use tower::ServiceExt;

    use super::{AppConfig, AppState, router};
    use crate::store::InMemorySandboxStore;

    fn app() -> axum::Router {
        router(AppState {
            store: Arc::new(InMemorySandboxStore::default()),
            config: AppConfig::default(),
        })
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
}
