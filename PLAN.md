# Deploy Automation Plan – Phase 2

Phase 1 is complete: we can provision EC2 hosts with nginx/Docker, deploy code + DB to the temporary IP, and manually swap the Elastic IP once testing passes. Phase 2 focuses solely on HTTPS enablement and DNS/Certbot workflows so we can obtain, renew, and reset certificates without downtime.

## Remaining Goals
1. Serve the site over HTTPS (port 443) via nginx using Let's Encrypt certificates.
2. Provide a dedicated deployment-manager workflow to (re)issue certificates and update nginx without touching application code.
3. Make DNS switches explicit and safe—either by automating the swap or documenting the exact manual steps with pauses in the CLI.

## Deliverables
1. **TLS-Ready nginx config**
   - Update `nginx_config` to include an SSL server block listening on 443.
   - Support HTTP->HTTPS redirects, HSTS (optional), and separate config paths for cert/key files.
   - Ensure static/media aliases work identically for both 80 and 443.

2. **Certbot Integration**
   - Install Certbot + nginx plugin during provisioning (but skip cert issuance until explicitly requested).
   - Add CLI prompts to capture domain list (primary + SANs) and DNS challenge instructions if needed.
   - Store cert paths in `config/provisioning.json` so they remain consistent.

3. **New deployment-manager commands**
   - `Setup HTTPS / Issue Certs`: stops nginx, runs Certbot for configured domains, updates config, restarts nginx.
   - `Reset Let's Encrypt`: kills any running Certbot process, removes stale cert files, reissues certificates.
   - Optional: `Renew HTTPS Certs` for cron-style manual runs (if we don't rely on certbot.timer).

4. **DNS Workflow Hook**
   - Provide a helper or documented prompt (option) that reminds the operator to update DNS records before hitting Certbot if using HTTP-01, or to add TXT records when using DNS-01.
   - Pause the CLI until the user confirms DNS propagation.

5. **Config & Docs**
   - Extend `config/provisioning.json` with `primary_domain`, `alt_domains`, `cert_path`, `key_path`, and challenge type.
   - Update `DEPLOYMENT-INSTRUCTIONS.md` with step-by-step HTTPS setup, renewal processes, and rollback instructions.

## Validation & Testing
- Dry run Certbot issuance using staging environment (Let's Encrypt staging endpoint) before going live.
- Verify nginx reloads cleanly and both HTTP/HTTPS flows work.
- Confirm that `Reset Let's Encrypt` stops/cleans existing jobs and reissues successfully without leaving partial state.

## Open Questions
- Challenge method: HTTP-01 vs DNS-01 (DNS-01 avoids downtime but needs automation of TXT records).
- Whether to automate DNS changes (e.g., Route53) or keep them manual but guided.
- Handling certificate renewals automatically via systemd timers vs manual CLI command.
