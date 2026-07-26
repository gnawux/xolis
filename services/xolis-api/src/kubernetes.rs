use std::{collections::BTreeMap, fmt::Write};

use async_trait::async_trait;
use chrono::{DateTime, SecondsFormat, Utc};
use kube::{
    Api, Client, Error as KubeError,
    api::{DeleteParams, ListParams, PostParams},
    core::{ApiResource, DynamicObject, GroupVersionKind},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::{
    domain::{CreateSandboxCommand, Sandbox, SandboxState},
    store::{SandboxStore, StoreError},
};

const TENANT_HASH_LABEL: &str = "xolis.io/tenant-hash";
const PROFILE_LABEL: &str = "xolis.io/profile";
const IDEMPOTENCY_HASH_LABEL: &str = "xolis.io/idempotency-hash";
const METADATA_ANNOTATION: &str = "xolis.io/request-metadata";

#[derive(Clone)]
pub struct KubernetesSandboxStore {
    claims: Api<DynamicObject>,
    warm_pool: String,
    profile: String,
}

impl KubernetesSandboxStore {
    pub fn new(
        client: Client,
        namespace: &str,
        warm_pool: impl Into<String>,
        profile: impl Into<String>,
    ) -> Self {
        let claims = Api::namespaced_with(client, namespace, &claim_resource());
        Self {
            claims,
            warm_pool: warm_pool.into(),
            profile: profile.into(),
        }
    }
}

#[async_trait]
impl SandboxStore for KubernetesSandboxStore {
    async fn create(&self, command: CreateSandboxCommand) -> Result<Sandbox, StoreError> {
        if command.profile != self.profile {
            return Err(StoreError::Conflict(format!(
                "profile {} is not configured by this store",
                command.profile
            )));
        }

        let tenant_id = command.tenant_id.clone();
        let claim = build_claim(&command, &self.warm_pool)?;
        let name = claim
            .metadata
            .name
            .as_deref()
            .ok_or_else(|| StoreError::Unavailable("claim name is missing".to_owned()))?;

        let created = match self.claims.create(&PostParams::default(), &claim).await {
            Ok(created) => created,
            Err(KubeError::Api(response)) if response.code == 409 => {
                self.claims.get(name).await.map_err(map_kube_error)?
            }
            Err(error) => return Err(map_kube_error(error)),
        };
        claim_to_sandbox(&created, &tenant_id)
    }

    async fn list(&self, tenant_id: &str) -> Result<Vec<Sandbox>, StoreError> {
        let selector = format!("{TENANT_HASH_LABEL}={}", tenant_hash(tenant_id));
        let claims = self
            .claims
            .list(&ListParams::default().labels(&selector))
            .await
            .map_err(map_kube_error)?;
        let mut sandboxes = claims
            .items
            .iter()
            .map(|claim| claim_to_sandbox(claim, tenant_id))
            .collect::<Result<Vec<_>, _>>()?;
        sandboxes.sort_by_key(|sandbox| sandbox.created_at);
        Ok(sandboxes)
    }

    async fn get(&self, tenant_id: &str, id: &str) -> Result<Sandbox, StoreError> {
        let claim = self.claims.get(id).await.map_err(map_kube_error)?;
        claim_to_sandbox(&claim, tenant_id)
    }

    async fn delete(&self, tenant_id: &str, id: &str) -> Result<(), StoreError> {
        let claim = self.claims.get(id).await.map_err(map_kube_error)?;
        verify_tenant(&claim, tenant_id)?;
        self.claims
            .delete(id, &DeleteParams::foreground())
            .await
            .map_err(map_kube_error)?;
        Ok(())
    }
}

fn claim_resource() -> ApiResource {
    ApiResource::from_gvk(&GroupVersionKind::gvk(
        "extensions.agents.x-k8s.io",
        "v1beta1",
        "SandboxClaim",
    ))
}

fn build_claim(
    command: &CreateSandboxCommand,
    warm_pool: &str,
) -> Result<DynamicObject, StoreError> {
    let tenant_hash = tenant_hash(&command.tenant_id);
    let idempotency_hash = command
        .idempotency_key
        .as_deref()
        .map(|key| idempotency_hash(&command.tenant_id, key));
    let name = idempotency_hash
        .as_ref()
        .map(|hash| format!("xolis-{}", &hash[..24]))
        .unwrap_or_else(|| format!("xolis-{}", uuid::Uuid::new_v4()));
    let expires_at = Utc::now()
        + chrono::Duration::seconds(
            i64::try_from(command.ttl_seconds)
                .map_err(|_| StoreError::Conflict("TTL is too large".to_owned()))?,
        );

    let mut labels = BTreeMap::from([
        (TENANT_HASH_LABEL.to_owned(), tenant_hash.clone()),
        (PROFILE_LABEL.to_owned(), command.profile.clone()),
    ]);
    if let Some(hash) = idempotency_hash {
        labels.insert(IDEMPOTENCY_HASH_LABEL.to_owned(), hash);
    }

    let mut claim = DynamicObject::new(&name, &claim_resource());
    claim.metadata.labels = Some(labels);
    claim.metadata.annotations = Some(BTreeMap::from([(
        METADATA_ANNOTATION.to_owned(),
        serde_json::to_string(&command.metadata)
            .map_err(|error| StoreError::Conflict(error.to_string()))?,
    )]));
    claim.data = json!({
        "spec": {
            "warmPoolRef": {"name": warm_pool},
            "lifecycle": {
                "shutdownTime": expires_at.to_rfc3339_opts(SecondsFormat::Secs, true),
                "shutdownPolicy": "DeleteForeground"
            },
            "additionalPodMetadata": {
                "labels": {
                    (TENANT_HASH_LABEL): tenant_hash,
                    (PROFILE_LABEL): command.profile
                }
            }
        }
    });
    Ok(claim)
}

fn claim_to_sandbox(claim: &DynamicObject, tenant_id: &str) -> Result<Sandbox, StoreError> {
    verify_tenant(claim, tenant_id)?;

    let id = claim
        .metadata
        .name
        .clone()
        .ok_or_else(|| StoreError::Unavailable("claim name is missing".to_owned()))?;
    let created_at = claim
        .metadata
        .creation_timestamp
        .as_ref()
        .ok_or_else(|| StoreError::Unavailable("claim creation timestamp is missing".to_owned()))?
        .0
        .to_string()
        .parse::<DateTime<Utc>>()
        .map_err(|error| {
            StoreError::Unavailable(format!("invalid claim creation timestamp: {error}"))
        })?;
    let expires_at = claim
        .data
        .pointer("/spec/lifecycle/shutdownTime")
        .and_then(Value::as_str)
        .ok_or_else(|| StoreError::Unavailable("claim shutdown time is missing".to_owned()))?
        .parse::<DateTime<Utc>>()
        .map_err(|error| {
            StoreError::Unavailable(format!("invalid claim shutdown time: {error}"))
        })?;
    let profile = claim
        .metadata
        .labels
        .as_ref()
        .and_then(|labels| labels.get(PROFILE_LABEL))
        .cloned()
        .ok_or_else(|| StoreError::Unavailable("claim profile label is missing".to_owned()))?;
    let metadata = claim
        .metadata
        .annotations
        .as_ref()
        .and_then(|annotations| annotations.get(METADATA_ANNOTATION))
        .map(|value| serde_json::from_str::<BTreeMap<String, String>>(value))
        .transpose()
        .map_err(|error| StoreError::Unavailable(format!("invalid claim metadata: {error}")))?
        .unwrap_or_default();
    let runtime_id = claim
        .data
        .pointer("/status/sandbox/name")
        .and_then(Value::as_str)
        .map(str::to_owned);

    let (state, reason) = claim_state(claim);
    Ok(Sandbox {
        id,
        tenant_id: tenant_id.to_owned(),
        runtime_id,
        profile,
        state,
        created_at,
        expires_at,
        metadata,
        reason,
    })
}

fn verify_tenant(claim: &DynamicObject, tenant_id: &str) -> Result<(), StoreError> {
    let matches = claim
        .metadata
        .labels
        .as_ref()
        .and_then(|labels| labels.get(TENANT_HASH_LABEL))
        .is_some_and(|value| value == &tenant_hash(tenant_id));
    if matches {
        Ok(())
    } else {
        Err(StoreError::NotFound)
    }
}

fn claim_state(claim: &DynamicObject) -> (SandboxState, Option<String>) {
    if claim.metadata.deletion_timestamp.is_some() {
        return (SandboxState::Terminating, None);
    }

    let ready = claim
        .data
        .pointer("/status/conditions")
        .and_then(Value::as_array)
        .and_then(|conditions| {
            conditions
                .iter()
                .find(|condition| condition.get("type").and_then(Value::as_str) == Some("Ready"))
        });
    let Some(condition) = ready else {
        return (SandboxState::Pending, None);
    };

    let status = condition.get("status").and_then(Value::as_str);
    let reason = condition
        .get("reason")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let message = condition
        .get("message")
        .and_then(Value::as_str)
        .filter(|message| !message.is_empty());
    let detail = match (reason.as_deref(), message) {
        (Some(reason), Some(message)) => Some(format!("{reason}: {message}")),
        (Some(reason), None) => Some(reason.to_owned()),
        (None, Some(message)) => Some(message.to_owned()),
        (None, None) => None,
    };

    if status == Some("True") {
        return (SandboxState::Running, detail);
    }
    match reason.as_deref() {
        Some("ClaimExpired" | "Expired") => (SandboxState::Expired, detail),
        Some("ReconcilerError" | "InvalidMetadata" | "TemplateNotFound" | "WarmPoolNotFound") => {
            (SandboxState::Failed, detail)
        }
        _ => (SandboxState::Pending, detail),
    }
}

fn tenant_hash(tenant_id: &str) -> String {
    digest(tenant_id.as_bytes())[..32].to_owned()
}

fn idempotency_hash(tenant_id: &str, key: &str) -> String {
    let mut input = tenant_id.as_bytes().to_vec();
    input.push(0);
    input.extend_from_slice(key.as_bytes());
    digest(&input)
}

fn digest(input: &[u8]) -> String {
    Sha256::digest(input)
        .iter()
        .fold(String::with_capacity(64), |mut output, byte| {
            write!(&mut output, "{byte:02x}").expect("writing to a String cannot fail");
            output
        })
}

fn map_kube_error(error: KubeError) -> StoreError {
    match error {
        KubeError::Api(response) if response.code == 404 => StoreError::NotFound,
        KubeError::Api(response) if response.code == 409 => StoreError::Conflict(response.message),
        other => StoreError::Unavailable(other.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use chrono::{TimeDelta, Utc};
    use k8s_openapi::apimachinery::pkg::apis::meta::v1::Time;
    use serde_json::json;

    use super::{build_claim, claim_to_sandbox, idempotency_hash, tenant_hash};
    use crate::domain::{CreateSandboxCommand, SandboxState};

    fn command() -> CreateSandboxCommand {
        CreateSandboxCommand {
            tenant_id: "tenant-a".to_owned(),
            idempotency_key: Some("request-1".to_owned()),
            profile: "python-basic-v1".to_owned(),
            ttl_seconds: 300,
            metadata: BTreeMap::from([("requestId".to_owned(), "demo-001".to_owned())]),
        }
    }

    fn kube_time(value: chrono::DateTime<Utc>) -> Time {
        Time(value.to_rfc3339().parse().expect("Kubernetes timestamp"))
    }

    #[test]
    fn claim_uses_pinned_beta_api_and_lifecycle_policy() {
        let claim = build_claim(&command(), "python-basic-v1-pool").expect("claim");
        let expected_hash = idempotency_hash("tenant-a", "request-1");
        let expected_name = format!("xolis-{}", &expected_hash[..24]);

        assert_eq!(
            claim.types.as_ref().expect("type metadata").api_version,
            "extensions.agents.x-k8s.io/v1beta1"
        );
        assert_eq!(
            claim.types.as_ref().expect("type metadata").kind,
            "SandboxClaim"
        );
        assert_eq!(claim.metadata.name.as_deref(), Some(expected_name.as_str()));
        assert_eq!(
            claim.data["spec"]["warmPoolRef"]["name"],
            "python-basic-v1-pool"
        );
        assert_eq!(
            claim.data["spec"]["lifecycle"]["shutdownPolicy"],
            "DeleteForeground"
        );
        assert_eq!(
            claim.data["spec"]["additionalPodMetadata"]["labels"]["xolis.io/tenant-hash"],
            tenant_hash("tenant-a")
        );
    }

    #[test]
    fn ready_condition_maps_to_running_state() {
        let mut claim = build_claim(&command(), "python-basic-v1-pool").expect("claim");
        let now = Utc::now();
        claim.metadata.creation_timestamp = Some(kube_time(now));
        claim.data["status"] = json!({
            "sandbox": {"name": "sandbox-runtime-123"},
            "conditions": [{
                "type": "Ready",
                "status": "True",
                "reason": "SandboxReady",
                "message": "sandbox is ready"
            }]
        });

        let sandbox = claim_to_sandbox(&claim, "tenant-a").expect("sandbox");
        assert_eq!(sandbox.state, SandboxState::Running);
        assert_eq!(sandbox.runtime_id.as_deref(), Some("sandbox-runtime-123"));
        assert_eq!(
            sandbox.reason.as_deref(),
            Some("SandboxReady: sandbox is ready")
        );
        assert!(sandbox.expires_at >= now + TimeDelta::seconds(299));
        assert_eq!(
            sandbox.metadata.get("requestId").map(String::as_str),
            Some("demo-001")
        );
    }

    #[test]
    fn failures_expiration_and_deletion_map_to_public_states() {
        let now = Utc::now();
        let mut claim = build_claim(&command(), "python-basic-v1-pool").expect("claim");
        claim.metadata.creation_timestamp = Some(kube_time(now));
        claim.data["status"] = json!({
            "conditions": [{"type": "Ready", "status": "False", "reason": "TemplateNotFound"}]
        });
        assert_eq!(
            claim_to_sandbox(&claim, "tenant-a").expect("failed").state,
            SandboxState::Failed
        );

        claim.data["status"] = json!({
            "conditions": [{"type": "Ready", "status": "False", "reason": "ClaimExpired"}]
        });
        assert_eq!(
            claim_to_sandbox(&claim, "tenant-a").expect("expired").state,
            SandboxState::Expired
        );

        claim.metadata.deletion_timestamp = Some(kube_time(now));
        assert_eq!(
            claim_to_sandbox(&claim, "tenant-a")
                .expect("terminating")
                .state,
            SandboxState::Terminating
        );
    }

    #[test]
    fn tenant_mismatch_is_hidden_as_not_found() {
        let mut claim = build_claim(&command(), "python-basic-v1-pool").expect("claim");
        claim.metadata.creation_timestamp = Some(kube_time(Utc::now()));
        let error = claim_to_sandbox(&claim, "tenant-b").expect_err("tenant mismatch");
        assert_eq!(error.to_string(), "sandbox not found");
    }
}
