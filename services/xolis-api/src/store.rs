use std::{collections::HashMap, sync::Arc};

use async_trait::async_trait;
use chrono::{Duration, Utc};
use thiserror::Error;
use tokio::sync::RwLock;
use uuid::Uuid;

use crate::domain::{CreateSandboxCommand, Sandbox, SandboxState};

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("sandbox not found")]
    NotFound,
    #[error("sandbox store conflict: {0}")]
    Conflict(String),
    #[error("sandbox store unavailable: {0}")]
    Unavailable(String),
}

#[async_trait]
pub trait SandboxStore: Send + Sync {
    async fn create(&self, command: CreateSandboxCommand) -> Result<Sandbox, StoreError>;
    async fn list(&self, tenant_id: &str) -> Result<Vec<Sandbox>, StoreError>;
    async fn get(&self, tenant_id: &str, id: &str) -> Result<Sandbox, StoreError>;
    async fn delete(&self, tenant_id: &str, id: &str) -> Result<(), StoreError>;
}

#[derive(Default)]
struct InMemoryState {
    sandboxes: HashMap<String, Sandbox>,
    idempotency: HashMap<(String, String), String>,
}

#[derive(Clone, Default)]
pub struct InMemorySandboxStore {
    state: Arc<RwLock<InMemoryState>>,
}

#[async_trait]
impl SandboxStore for InMemorySandboxStore {
    async fn create(&self, command: CreateSandboxCommand) -> Result<Sandbox, StoreError> {
        let mut state = self.state.write().await;

        if let Some(key) = command.idempotency_key.as_ref() {
            let lookup = (command.tenant_id.clone(), key.clone());
            if let Some(id) = state.idempotency.get(&lookup) {
                return state
                    .sandboxes
                    .get(id)
                    .cloned()
                    .ok_or_else(|| StoreError::Conflict("stale idempotency record".to_owned()));
            }
        }

        let now = Utc::now();
        let id = Uuid::new_v4().to_string();
        let sandbox = Sandbox {
            id: id.clone(),
            tenant_id: command.tenant_id.clone(),
            runtime_id: Some(id),
            profile: command.profile,
            state: SandboxState::Pending,
            created_at: now,
            expires_at: now + Duration::seconds(command.ttl_seconds as i64),
            metadata: command.metadata,
            reason: None,
        };

        if let Some(key) = command.idempotency_key {
            state
                .idempotency
                .insert((command.tenant_id, key), sandbox.id.clone());
        }
        state.sandboxes.insert(sandbox.id.clone(), sandbox.clone());

        Ok(sandbox)
    }

    async fn list(&self, tenant_id: &str) -> Result<Vec<Sandbox>, StoreError> {
        let state = self.state.read().await;
        let mut sandboxes = state
            .sandboxes
            .values()
            .filter(|sandbox| sandbox.tenant_id == tenant_id)
            .cloned()
            .collect::<Vec<_>>();
        sandboxes.sort_by_key(|sandbox| sandbox.created_at);
        Ok(sandboxes)
    }

    async fn get(&self, tenant_id: &str, id: &str) -> Result<Sandbox, StoreError> {
        let state = self.state.read().await;
        state
            .sandboxes
            .get(id)
            .filter(|sandbox| sandbox.tenant_id == tenant_id)
            .cloned()
            .ok_or(StoreError::NotFound)
    }

    async fn delete(&self, tenant_id: &str, id: &str) -> Result<(), StoreError> {
        let mut state = self.state.write().await;
        let sandbox = state
            .sandboxes
            .get(id)
            .filter(|sandbox| sandbox.tenant_id == tenant_id)
            .cloned()
            .ok_or(StoreError::NotFound)?;

        state.sandboxes.remove(id);
        state.idempotency.retain(|_, value| value != &sandbox.id);
        Ok(())
    }
}

#[cfg(test)]
impl InMemorySandboxStore {
    pub async fn mark_running(&self, id: &str) {
        let mut state = self.state.write().await;
        let sandbox = state.sandboxes.get_mut(id).expect("test sandbox");
        sandbox.state = SandboxState::Running;
    }
}
