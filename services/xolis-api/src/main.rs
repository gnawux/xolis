use std::{env, net::SocketAddr, sync::Arc};

use tracing::info;
use tracing_subscriber::EnvFilter;
use xolis_api::{
    AppConfig, AppState, InMemorySandboxStore, KubernetesSandboxStore, SandboxStore, router,
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

    let config = AppConfig {
        maximum_ttl_seconds,
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

    let state = AppState { store, config };
    let listener = tokio::net::TcpListener::bind(address).await?;
    info!(listen_address = %address, "xolis API listening");
    axum::serve(listener, router(state)).await?;
    Ok(())
}
