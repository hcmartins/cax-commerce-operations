#!/usr/bin/env bash
# One-time setup: lets .github/workflows/ci-cd.yml authenticate to Azure via
# OIDC (azure/login) — no client secret stored anywhere, GitHub and Azure AD
# trust each other's tokens directly for this one app registration.
#
# rg-commerce-dev is a SHARED resource group (commerce-intelligence-api and
# its own infra live in it too), so this deliberately grants the narrowest
# scope that works: Reader on the resource group, Container Apps Contributor
# scoped to just this app's own Container App (not the whole environment or
# any other service's), and AcrPush on the shared registry (push+pull, no
# per-repository scoping exists to narrow further). This identity can never
# touch commerce-intelligence-api, the shared storage account, or any other
# service's Container App.
#
# Two grants go beyond commerce-intelligence's equivalent script
# (infra/setup-github-oidc.sh in that repo), because this app keeps database
# migration as a distinct one-shot step run from CI rather than migrating on
# container startup (see commerce-infrastructure/docs/architecture.md):
#   - Key Vault Secrets User, vault-scoped (Azure RBAC has no finer-grained,
#     per-secret built-in role) — needed to read commerce-operations-database-url
#     to run migrations.
#   - Contributor, scoped to just the Postgres server resource (not the
#     resource group) — needed to create/delete the temporary per-run firewall
#     rule that lets the GitHub-hosted runner reach Postgres to run migrations.
#
# A third grant, easy to miss: Managed Identity Operator, scoped to this
# app's own identity resource (id-commerce-operations-api). `az containerapp
# update` resubmits the Container App's full definition, including its
# `identity.userAssignedIdentities` reference — Azure Resource Manager's
# "linked authorization" check then requires the caller to hold
# `Microsoft.ManagedIdentity/userAssignedIdentities/assign/action` on that
# referenced identity. Container Apps Contributor does not include this
# action (verified: `az role definition list --name "Container Apps
# Contributor"` has no Microsoft.ManagedIdentity/* entries at all), so
# without this grant every deploy fails with a linked-authorization error
# even though the Container App write itself would otherwise be permitted.
#
# Two federated credentials are created per subject (name-based AND
# immutable-ID-based), not one: GitHub began issuing OIDC tokens with an
# immutable-subject format (repo:<owner>@<owner_id>/<repo>@<repo_id>:...)
# automatically for repositories created, renamed, or transferred on or
# after 2026-07-15 — see
# https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-github-actions-oidc-tokens/
# and https://learn.microsoft.com/en-us/entra/workload-id/workload-identities-github-immutable-subjects.
# A federated credential trusting only the old name-based subject fails
# with AADSTS700213 ("no matching federated identity record") against such
# a repo. This script creates both so it works whether or not the repo has
# opted in / been auto-migrated; keeping the name-based one alongside is
# harmless (GitHub only ever presents one subject per token, matched
# against whichever credential fits).
#
# Run once per repo, after the GitHub repository exists (the federated
# credentials below are scoped to its exact owner/name/IDs). Requires network
# access to api.github.com to resolve the owner/repo numeric IDs; for a
# private repository, export GITHUB_TOKEN so that lookup is authenticated.
#
# Usage:
#   GITHUB_OWNER=<org-or-user> GITHUB_REPO=<repo-name> \
#   RESOURCE_GROUP=rg-commerce-dev ACR_NAME=acrcommercedevzqbs3z \
#   KEY_VAULT_NAME=kv-commerce-dev-zqbs3z POSTGRES_SERVER_NAME=psql-commerce-dev-zqbs3z \
#   CONTAINER_APP_NAME=commerce-operations-api \
#   ./infrastructure/setup-github-oidc.sh

set -euo pipefail

# Disable Git Bash's automatic POSIX-to-Windows path conversion for this
# script's lifetime — without it, MSYS silently rewrites any argument that
# looks like an absolute path (e.g. --scope /subscriptions/...) into a
# garbled Windows path before az ever sees it, and `az role assignment
# create` fails with a cryptic "MissingSubscription" error. No-op outside
# Git Bash on Windows.
export MSYS_NO_PATHCONV=1

: "${GITHUB_OWNER:?Set GITHUB_OWNER (the GitHub org or user that owns the repo)}"
: "${GITHUB_REPO:?Set GITHUB_REPO (repo name only, no owner prefix)}"
: "${RESOURCE_GROUP:?Set RESOURCE_GROUP (e.g. rg-commerce-dev)}"
: "${ACR_NAME:?Set ACR_NAME (e.g. acrcommercedevzqbs3z)}"
: "${KEY_VAULT_NAME:?Set KEY_VAULT_NAME (e.g. kv-commerce-dev-zqbs3z)}"
: "${POSTGRES_SERVER_NAME:?Set POSTGRES_SERVER_NAME (e.g. psql-commerce-dev-zqbs3z)}"
CONTAINER_APP_NAME="${CONTAINER_APP_NAME:-commerce-operations-api}"

APP_NAME="commerce-operations-github-actions"
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

echo "=== App registration ==="
APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -z "$APP_ID" ]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
  az ad sp create --id "$APP_ID" >/dev/null
  echo "Created app $APP_ID"
else
  echo "Reusing existing app $APP_ID"
fi

# Resolve GitHub's immutable owner/repo IDs (best-effort — falls back to
# name-based-only credentials if this repo can't be reached, e.g. offline).
echo "=== Resolving GitHub owner/repo IDs for immutable subjects ==="
GH_AUTH_HEADER=()
if [ -n "${GITHUB_TOKEN:-}" ]; then
  GH_AUTH_HEADER=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
fi
GH_REPO_JSON=$(curl -s "${GH_AUTH_HEADER[@]}" "https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}" || true)
OWNER_ID=$(echo "$GH_REPO_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('owner',{}).get('id',''))" 2>/dev/null || true)
REPO_ID=$(echo "$GH_REPO_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || true)
if [ -n "$OWNER_ID" ] && [ -n "$REPO_ID" ]; then
  echo "  owner_id=$OWNER_ID repo_id=$REPO_ID"
else
  echo "  Could not resolve owner/repo IDs — only name-based credentials will be created."
  echo "  If this repo was created/renamed/transferred on or after 2026-07-15, GitHub may"
  echo "  issue immutable-subject tokens by default and login will fail with AADSTS700213"
  echo "  until you re-run this script (or add the immutable credentials manually)."
fi

# Two federated credentials per subject — see the comment block above for why
# both the name-based and immutable-ID-based forms are created.
echo "=== Federated credentials ==="
create_fic() {
  local name=$1 subject=$2
  if az ad app federated-credential list --id "$APP_ID" --query "[?name=='$name']" -o tsv | grep -q .; then
    echo "  $name already exists"
  else
    az ad app federated-credential create --id "$APP_ID" --parameters "{
      \"name\": \"$name\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"$subject\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }" >/dev/null
    echo "  created $name ($subject)"
  fi
}
create_fic "main-branch"        "repo:${GITHUB_OWNER}/${GITHUB_REPO}:ref:refs/heads/main"
create_fic "dev-environment"    "repo:${GITHUB_OWNER}/${GITHUB_REPO}:environment:dev"
create_fic "prod-environment"   "repo:${GITHUB_OWNER}/${GITHUB_REPO}:environment:production"
if [ -n "$OWNER_ID" ] && [ -n "$REPO_ID" ]; then
  IMMUTABLE="${GITHUB_OWNER}@${OWNER_ID}/${GITHUB_REPO}@${REPO_ID}"
  create_fic "main-branch-immutable"      "repo:${IMMUTABLE}:ref:refs/heads/main"
  create_fic "dev-environment-immutable"  "repo:${IMMUTABLE}:environment:dev"
  create_fic "prod-environment-immutable" "repo:${IMMUTABLE}:environment:production"
fi

echo "=== RBAC (scoped to this app only — rg-commerce-dev is shared) ==="
RG_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}"
ACR_ID=$(az acr show -g "$RESOURCE_GROUP" -n "$ACR_NAME" --query id -o tsv)
CONTAINER_APP_ID=$(az containerapp show -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" --query id -o tsv)
KEY_VAULT_ID=$(az keyvault show -g "$RESOURCE_GROUP" -n "$KEY_VAULT_NAME" --query id -o tsv)
POSTGRES_ID=$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$POSTGRES_SERVER_NAME" --query id -o tsv)
IDENTITY_ID=$(az identity show -g "$RESOURCE_GROUP" -n "id-${CONTAINER_APP_NAME}" --query id -o tsv)
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)

# Role assignments target the service principal's object ID, not the app ID —
# `--assignee <appId>` intermittently fails to resolve right after the SP is
# created. A failed `create` here is verified against a real `list`, not
# assumed to mean "already assigned": that assumption is exactly what
# previously hid a real failure (Git Bash's path-mangling breaking every
# `--scope`, invisible because every failure printed the same reassuring
# fallback message).
assign_role() {
  local role=$1 scope=$2
  if az role assignment create --role "$role" --assignee "$SP_ID" --scope "$scope" -o none 2>/dev/null; then
    echo "  $role: created"
  elif az role assignment list --assignee "$SP_ID" --scope "$scope" --query "[?roleDefinitionName=='$role']" -o tsv | grep -q .; then
    echo "  $role: already assigned"
  else
    echo "  $role: FAILED — not present after create attempt. Scope: $scope" >&2
    return 1
  fi
}
assign_role "Reader" "$RG_ID"
assign_role "AcrPush" "$ACR_ID"
assign_role "Container Apps Contributor" "$CONTAINER_APP_ID"
assign_role "Key Vault Secrets User" "$KEY_VAULT_ID"
assign_role "Contributor" "$POSTGRES_ID"
# Closes the "linked authorization" gap: az containerapp update resubmits the
# Container App's identity reference, which ARM checks against this action —
# see the comment block above this script's header.
assign_role "Managed Identity Operator" "$IDENTITY_ID"

ACR_LOGIN_SERVER=$(az acr show -g "$RESOURCE_GROUP" -n "$ACR_NAME" --query loginServer -o tsv)

cat <<EOF

=== Done. Now configure the GitHub repo ===

Settings -> Secrets and variables -> Actions -> Secrets:
  AZURE_CLIENT_ID        $APP_ID
  AZURE_TENANT_ID        $TENANT_ID
  AZURE_SUBSCRIPTION_ID  $SUBSCRIPTION_ID

Settings -> Secrets and variables -> Actions -> Variables:
  ACR_NAME               $ACR_NAME
  ACR_LOGIN_SERVER       $ACR_LOGIN_SERVER
  AZURE_RESOURCE_GROUP   $RESOURCE_GROUP
  KEY_VAULT_NAME         $KEY_VAULT_NAME
  POSTGRES_SERVER_NAME   $POSTGRES_SERVER_NAME

Settings -> Environments:
  "dev"          — no protection rules needed (auto-deploys on merge to main)
  "production"   — add required reviewers here; this is what turns
                   deploy-prod into a manual-approval gate. Leave
                   PROD_AZURE_RESOURCE_GROUP / PROD_KEY_VAULT_NAME unset as
                   variables until PROD infrastructure actually exists —
                   deploy-prod skips cleanly without them.
EOF
