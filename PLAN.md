# Deploy Automation Plan

## Overview
We are splitting this effort into two phases. Phase 1 focuses on provisioning AWS infrastructure (instance + networking) and configuring nginx so deployments are reproducible end-to-end from a clean slate. Phase 2 layers in DNS / Let's Encrypt workflows, including a reusable "reset Let's Encrypt" command inside `deployment-manager.py`.

Assumptions from the user:
- Stay with Python-based automation (extend `deployment-manager.py` + boto3/AWS CLI) rather than switching to Terraform/CloudFormation.
- All provisioning parameters (region, AMI ID, instance type, volume sizes, security group IDs/names, Elastic IP allocation ID, etc.) must be driven by config entries (e.g., `.env` or a dedicated config file) so we can change them without code edits.
- Elastic IP already exists; automation should re-associate it with the new instance but never creates another one.
- Phase 2 (DNS + Let's Encrypt) can be done after Phase 1 is complete.

## Phase 1 – Automated Provisioning & Reverse Proxy
Goal: From a laptop, create a production-ready EC2 host (Ubuntu 24.04) with Docker, docker-compose, nginx reverse proxy, Gunicorn, and app files, while preparing (but not yet executing) the Elastic IP swap. Provisioning should be idempotent and callable via deployment-manager, and must leave the new instance testable via its temporary public IP. The Elastic IP reassociation will be a separate, explicitly-invoked step the user can run only after manual testing.

### Deliverables
1. **Configuration schema** – Expand `.env` (or dedicated config) with:
   - AWS region & profile, AMI ID, instance type, root volume size, optional data volume size
   - Security group IDs/names (or definitions for new ones), key pair name, subnet ID/VPC ID
   - Elastic IP allocation ID or address to re-associate
   - Default disk usage thresholds, tags, etc.
   Provide validation + friendly errors on missing values.

2. **Instance lifecycle helpers** – New Python helpers (boto3 preferred; fall back to AWS CLI if necessary) that:
   - Launch an EC2 instance with the configured instance type + block devices + tags
   - Wait for running state and fetch public DNS/IP
   - Attach or create additional EBS volume if requested, format/mount to `/app/data`
   - Output clear instructions + data needed for the later Elastic IP swap (allocation ID, current association)
   - Optionally terminate the previous instance (manual confirmation)

3. **Security group automation** – Update provisioning flow to either create dedicated SGs or reconfigure the existing one using config-driven port definitions (22, 80, 443, Django, Meilisearch). Ensure idempotent rule creation and IPv6 coverage if needed.

4. **Remote bootstrap** – After SSH connectivity succeeds:
   - Install Docker + docker compose plugin and required packages (same logic as today but resilient to reruns)
   - Install/enable nginx as a reverse proxy in front of Gunicorn (manage `/etc/nginx/sites-available/vdw` template that proxies to Docker/Gunicorn, listens on 80, and prepares for TLS)
   - Create systemd services or ensure docker-compose handles gunicorn; confirm log locations
   - Upload application code (reuse existing SCP flow) and seed `.env` on server
   - Start containers + nginx, verify HTTP 200 on health endpoint

5. **Deployment-manager workflow changes** – Introduce a "Phase 1" menu command (e.g., `Provision + Bootstrap Server`) which orchestrates the above steps. Break the logic into small methods we can reuse later (instance creation, security group config, remote bootstrap). Add a *separate* menu command for "Associate Elastic IP" so the operator can manually trigger the swap once validation is complete.

6. **Documentation / runbooks** – Update `DEPLOYMENT-INSTRUCTIONS.md` (and any other relevant docs) to describe the new config variables and provisioning workflow. Include rollback instructions (e.g., how to swap the Elastic IP back) and disk-sizing rationale.

### Validation Tasks
- Dry run in a sandbox (or mock) to ensure boto3 commands are correct.
- Confirm remote script handles being re-run (e.g., existing nginx config doesn't break).
- Ensure logs or prompts highlight manual checkpoints (e.g., confirm before terminating old instance).

## Phase 2 – DNS & Let's Encrypt Automation
Goal: After Phase 1 baseline works, automate DNS switching and Let's Encrypt certificate management, including a "reset Let's Encrypt" workflow that can stop/remove an existing certbot run and reissue certificates safely.

### Planned Deliverables
1. **DNS orchestration** – (Optional) helper for updating Bluehost or Route53 records; at minimum, document the manual step and ensure deployment-manager can pause for verification.
2. **nginx + Certbot integration** – Extend provisioning to install Certbot, request certificates for configured domains, and update nginx to serve HTTPS.
3. **Reusable LE workflow** – Add a deployment-manager command (Phase 2) called "Reset Let's Encrypt" that:
   - Stops nginx/certbot as needed
   - Removes old certificate files if necessary
   - Re-runs Certbot with the configured challenge method (likely DNS-01 to avoid downtime)
   - Reloads nginx and verifies certificate expiry dates
4. **Config-driven domains** – Add `PRIMARY_DOMAIN`, `ALT_DOMAINS`, and certificate storage paths to config; enforce that missing values raise errors.
5. **Documentation/testing** – Update docs describing DNS cutover + LE reset, plus guidance on verifying certificates.

## Out of Scope (for now)
- Terraform/CloudFormation rewrite (unless future decision changes this)
- Additional AWS services (CloudWatch alarms, IAM hardening, etc.) beyond what's required for Phase 1 deliverables
- Automatic DNS updates or certificate issuance during Phase 1

## Open Questions / Follow-ups
- Confirm final AWS region, subnet, key pair, and disk sizing defaults (placeholders now; update once decided).
- Decide whether to store provisioning config in `.env` or a structured file (YAML/TOML) for clarity.
- Determine how aggressively we should tear down old infrastructure (automatic termination vs manual).
