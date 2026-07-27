use std::{env, net::SocketAddr, sync::Arc, time::Duration};

use tracing::info;
use tracing_subscriber::EnvFilter;
use xolis_api::{
    AppConfig, AppState, InMemorySandboxStore, KubernetesSandboxStore, RouterRuntimeClient,
    SandboxRuntime, SandboxStore, router,
};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| "xolis_api=info".into()),
        )
        .init();

    let address = env::var("XOLIS_LISTEN_ADDRESS")
        .unwrap_or_else(|_| "127.0.0.1:8080".to_owned())
        .parse::<SocketAddr>()?;
    let maximum_ttl_seconds = env::var("XOLIS_MAXIMUM_TTL_SECONDS")
        .ok()
        .map(|value| value.parse())
        .transpose()?
        .unwrap_or(7_200);
    let maximum_command_timeout_seconds = env::var("XOLIS_MAXIMUM_COMMAND_TIMEOUT_SECONDS")
        .ok()
        .map(|value| value.parse())
        .transpose()?
        .unwrap_or(300);
    let maximum_upload_bytes = env::var("XOLIS_MAXIMUM_UPLOAD_BYTES")
        .ok()
        .map(|value| value.parse())
        .transpose()?
        .unwrap_or(10 * 1024 * 1024);
    let profile = env::var("XOLIS_PROFILE").unwrap_or_else(|_| "python-basic-v1".to_owned());

    let config = AppConfig {
        profile,
        maximum_ttl_seconds,
        maximum_command_timeout_seconds,
        maximum_upload_bytes,
        ..AppConfig::default()
    };
    let store: Arc<dyn SandboxStore> = match env::var("XOLIS_STORE")
        .unwrap_or_else(|_| "memory".to_owned())
        .as_str()
    {
        "memory" => Arc::new(InMemorySandboxStore::default()),
        "kubernetes" => {
            let namespace = env::var("XOLIS_SANDBOX_NAMESPACE")
                .unwrap_or_else(|_| "xolis-sandboxes".to_owned());
            let warm_pool =
                env::var("XOLIS_WARM_POOL").unwrap_or_else(|_| "python-basic-v1-pool".to_owned());
            let client = kube::Client::try_default().await?;
            Arc::new(KubernetesSandboxStore::new(
                client,
                &namespace,
                warm_pool,
                config.profile.clone(),
            ))
        }
        value => return Err(format!("unsupported XOLIS_STORE value: {value}").into()),
    };

    let runtime: Arc<dyn SandboxRuntime> = Arc::new(RouterRuntimeClient::new(
        env::var("XOLIS_ROUTER_URL").unwrap_or_else(|_| "http://sandbox-router:8080".to_owned()),
        env::var("XOLIS_SANDBOX_NAMESPACE").unwrap_or_else(|_| "xolis-sandboxes".to_owned()),
        env::var("XOLIS_RUNTIME_PORT")
            .ok()
            .map(|value| value.parse())
            .transpose()?
            .unwrap_or(8888),
        Duration::from_secs(maximum_command_timeout_seconds + 10),
    )?);

    let state = AppState {
        store,
        runtime,
        config,
    };
    let listener = tokio::net::TcpListener::bind(address).await?;
    info!(listen_address = %address, "xolis API listening");
    axum::serve(listener, router(state)).await?;
    Ok(())
}
