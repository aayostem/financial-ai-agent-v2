# financial-ai — AWS infrastructure

One flat Terraform root. No Terragrunt, no dev/staging/prod folders, no
per-service micro-modules. If a second environment ever becomes real (not
hypothetical), the move is `-var-file=prod.tfvars`, not a second copy of
every file here.

## Files

| File | What it provisions |
|---|---|
| `state-backend/main.tf` | **Run once, manually, first.** S3 bucket + DynamoDB table for this stack's own state. |
| `versions.tf` | Provider requirements, partial S3 backend (values via `backend.hcl`) |
| `variables.tf` | Every input, in one place |
| `network.tf` | VPC via the standard community module — single NAT gateway by default |
| `eks.tf` | EKS cluster + one managed node group |
| `database.tf` | RDS Postgres (pgvector-ready, `CREATE EXTENSION vector;` after apply) |
| `cache.tf` | ElastiCache Redis, primary + 1 replica |
| `storage.tf` | 3 S3 buckets — versioned, encrypted, TLS-only, with lifecycle rules |
| `iam.tf` | 2 IRSA roles: External Secrets Operator, app pods |
| `secrets.tf` | Secrets Manager secret holding the composed `DATABASE_URL` etc. |
| `outputs.tf` | Everything you need to point `kubectl`/Helm at what got created |

## Run order

```bash
# 1. once, ever (unless the state backend itself needs to change)
cd state-backend && terraform init && terraform apply
# note the bucket/table names from the output

# 2. fill in backend.hcl from those outputs
cd ..
cp backend.hcl.example backend.hcl
# edit backend.hcl

# 3. the actual stack
terraform init -backend-config=backend.hcl
terraform plan
terraform apply

# 4. point kubectl at the new cluster
$(terraform output -raw configure_kubectl)

# 5. install the External Secrets Operator, then point it at
#    `secrets_manager_secret_arn` / `external_secrets_irsa_role_arn` from
#    the outputs -- that's what replaces the Helm chart's inline Secret
#    template for production, per the migration plan.

# 6. deploy the Helm chart against this cluster, pointing api/agent's
#    ServiceAccounts at `app_irsa_role_arn`.
```

## What's deliberately NOT here

- **No Karpenter.** One managed node group, fixed instance type. Revisit
  only if node-utilization data actually shows a need.
- **No multi-region.** One region, one stack. The "hardcoded region"
  problem in the original review doesn't apply here because there's no
  interpolation to hardcode — `versions.tf`'s backend block is partial by
  necessity, filled in once at `init` time.
- **No per-environment directories.** See `variables.tf`'s header comment.
