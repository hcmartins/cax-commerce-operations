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
# Run once per repo, after the GitHub repository exists (the federated
# credentials below are scoped to its exact owner/name).
#
# Usage:
#   GITHUB_OWNER=<org-or-user> GITHUB_REPO=<repo-name> \
#   RESOURCE_GROUP=rg-commerce-dev ACR_NAME=acrcommercedevzqbs3z \
#   KEY_VAULT_NAME=kv-commerce-dev-zqbs3z POSTGRES_SERVER_NAME=psql-commerce-dev-zqbs3z \
#   CONTAINER_APP_NAME=commerce-operations-api \
#   ./infrastructure/setup-github-oidc.sh

set -euo pipefail

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

# Three federated credentials, matching the three distinct OIDC subject
# claims the workflow's jobs present: the build-and-push job (no
# `environment:`, so its subject is the branch ref) and the two deploy jobs
# (each declares `environment:`, which changes the subject to that
# environment's name instead of the branch ref) — getting a subject wrong
# here is the most common cause of "no matching federated identity record".
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

echo "=== RBAC (scoped to this app only — rg-commerce-dev is shared) ==="
RG_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}"
ACR_ID=$(az acr show -g "$RESOURCE_GROUP" -n "$ACR_NAME" --query id -o tsv)
CONTAINER_APP_ID=$(az containerapp show -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" --query id -o tsv)
KEY_VAULT_ID=$(az keyvault show -g "$RESOURCE_GROUP" -n "$KEY_VAULT_NAME" --query id -o tsv)
POSTGRES_ID=$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$POSTGRES_SERVER_NAME" --query id -o tsv)

az role assignment create --role Reader --assignee "$APP_ID" --scope "$RG_ID" -o none || echo "  Reader already assigned"
az role assignment create --role AcrPush --assignee "$APP_ID" --scope "$ACR_ID" -o none || echo "  AcrPush already assigned"
az role assignment create --role "Container Apps Contributor" --assignee "$APP_ID" --scope "$CONTAINER_APP_ID" -o none || echo "  Container Apps Contributor already assigned"
az role assignment create --role "Key Vault Secrets User" --assignee "$APP_ID" --scope "$KEY_VAULT_ID" -o none || echo "  Key Vault Secrets User already assigned"
az role assignment create --role Contributor --assignee "$APP_ID" --scope "$POSTGRES_ID" -o none || echo "  Contributor (Postgres) already assigned"

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
