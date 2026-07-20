# Cloud Deploy Smoke Test

Date: 2026-06-17

Use this checklist when validating the lightweight control plane locally with
Docker or on a private cloud VM. It is for owner-only validation before giving
the endpoint to external users.

## 1. Prepare Environment

Create a cloud-control env file from the template. This project already has a
root `.env` for the desktop/backend runtime, so keep the control-plane deploy
settings separate:

```powershell
copy .env.cloud.example .env.cloud
```

Update these values before starting a cloud VM deployment:

- `CADDY_HOST`: real DNS name, for example `control.example.com`
- `SPIRITKIN_MANAGEMENT_TOKEN`: long random owner token
- `SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET`: long random signing secret
- `AWS_SECRET_ACCESS_KEY`: long random MinIO/S3 secret
- `SPIRITKIN_ARTIFACT_S3_BUCKET`: bucket name, default `spiritkin-artifacts`

For local Docker-only validation, `CADDY_HOST=localhost` is acceptable.

## 2. Static Config Checks

Validate the compose file without starting containers:

```powershell
docker compose --env-file .env.cloud config
```

Expected:

- Compose resolves without errors.
- `control-plane` has `SPIRITKIN_PRODUCTION_MODE=1`.
- `control-plane` receives `SPIRITKIN_MANAGEMENT_TOKEN`.
- `control-plane` receives `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
- `control-plane` direct port is bound to `127.0.0.1:8791` unless explicitly
  testing LAN access.
- MinIO console is bound to `127.0.0.1:9001`.

## 3. Start Stack

Build preflight:

```powershell
docker compose --env-file .env.cloud build control-plane
```

If this fails while resolving `python:3.12-slim`, fix the Docker registry mirror
or proxy first, then rerun the build. The compose configuration can be valid even
when the local Docker daemon cannot reach the configured image registry.

Start the stack:

```powershell
docker compose --env-file .env.cloud up -d --build
```

Inspect status:

```powershell
docker compose --env-file .env.cloud ps
docker compose --env-file .env.cloud logs --tail=100 control-plane
docker compose --env-file .env.cloud logs --tail=100 minio-init
```

Expected:

- `minio` is running.
- `minio-init` completed successfully.
- `control-plane` is running and healthy.
- `caddy` is running.

## 4. Health and Public URLs

Local direct health check:

```powershell
curl http://127.0.0.1:8791/android/health
```

Expected in production mode:

- JSON includes `ok: true`.
- JSON includes `production_mode: true`.
- JSON does not include the full `state` snapshot.

If using Caddy locally:

```powershell
curl http://localhost/android/health
```

If using a real cloud domain:

```powershell
curl https://<control-plane-host>/android/health
```

Expected:

- HTTPS works for the real domain.
- Public requests use the Caddy URL, not the raw `8791` port.

## 5. Owner Control Access

Open the control page:

```text
https://<control-plane-host>/control
```

Expected:

- Without token, the page renders the management-token bootstrap state.
- With `SPIRITKIN_MANAGEMENT_TOKEN`, owner actions are available.

Create an iOS/Web terminal token:

```powershell
curl -H "Authorization: Bearer <management-token>" `
  "https://<control-plane-host>/ios/control/pairing?workspace_id=local-ecommerce&device_role=ios_terminal&format=json"
```

Expected:

- Response includes `pairing_token`.
- `POST /ios/control/pair` can bind the terminal.
- Bound terminal token can read only its workspace snapshot.

## 6. Android Pairing and Screenshot

Create Android pairing:

```powershell
curl -H "Authorization: Bearer <management-token>" `
  "https://<control-plane-host>/ios/control/pairing?workspace_id=local-ecommerce&device_role=android_bridge&format=json"
```

Expected:

- Response includes `server_url`.
- `server_url` is `https://<control-plane-host>/android/link`.
- Response includes `deep_link` and `pairing_token`.

On the Android phone:

1. Install `mobile-link-bridge/out/mobile-link-bridge.apk` or download it from
   `https://<control-plane-host>/android/apk`.
2. Open `SpiritKin Control Bridge`.
3. Bind with the deep link or manual server/workspace/token fields.
4. Tap the connection check.
5. Enable background sync.

Screenshot artifact smoke:

1. From `/control`, queue `android.screenshot.request_permission`.
2. Approve Android MediaProjection consent on the phone.
3. Queue `android.screenshot.capture`.
4. Wait for heartbeat/result sync.

Expected:

- Command result reports success.
- Result includes an `artifact_id`.
- Artifact preview/download returns a PNG.
- Snapshot quota shows artifact byte/count usage.

## 7. Artifact Store and MinIO

Upload a small mobile artifact through the control UI or API:

```powershell
$body = @{
  workspace_id = "local-ecommerce"
  source = "smoke"
  files = @(@{ name = "smoke.txt"; text = "hello cloud" })
} | ConvertTo-Json -Depth 5

curl -Method POST `
  -H "Authorization: Bearer <management-token>" `
  -H "Content-Type: application/json" `
  -Body $body `
  "https://<control-plane-host>/mobile/artifacts"
```

Expected:

- Response includes `artifact_id`.
- Artifact backend is `s3`.
- Download URL works through the control plane.
- MinIO contains an object under
  `prod/local-ecommerce/<artifact_id>/smoke.txt`.

Cleanup smoke:

```powershell
$body = @{
  action = "cleanup_artifacts"
  workspace_id = "local-ecommerce"
  older_than_hours = 0
} | ConvertTo-Json

curl -Method POST `
  -H "Authorization: Bearer <management-token>" `
  -H "Content-Type: application/json" `
  -Body $body `
  "https://<control-plane-host>/ios/control/action"
```

Expected:

- Expired artifact status becomes `deleted`.
- Corresponding MinIO object is deleted.

## 8. Worker Package and Heartbeat

Check hosted Worker package manifest:

```powershell
curl "https://<control-plane-host>/worker/package/manifest"
```

Expected:

- Response includes package `spiritkin-control-plane-worker`.
- Response includes `download_url`.
- `serving_validation.status` is `ok`.

Create Worker pairing:

```powershell
curl -H "Authorization: Bearer <management-token>" `
  "https://<control-plane-host>/ios/control/pairing?workspace_id=local-ecommerce&device_role=remote_worker&format=json"
```

Expected:

- Response includes `package_manifest_url`.
- Response includes `setup_command`.
- A Worker started with the pairing token can bind and heartbeat.

## 9. Network Exposure Check

On the cloud VM firewall/security group:

- Allow inbound `80/tcp` and `443/tcp`.
- Do not expose `8791/tcp` publicly.
- Do not expose `9001/tcp` publicly.
- Keep SSH restricted to the operator IP when possible.

From a separate network:

```powershell
curl https://<control-plane-host>/android/health
curl http://<control-plane-host>:8791/android/health
curl http://<control-plane-host>:9001
```

Expected:

- HTTPS health works.
- Raw `8791` is blocked.
- Raw `9001` is blocked.

## 10. Stop and Inspect

Stop the stack:

```powershell
docker compose --env-file .env.cloud down
```

Preserve volumes while testing restart/recovery:

```powershell
docker compose --env-file .env.cloud up -d
```

Expected:

- State persists across restart.
- Artifacts remain available.
- Existing bound tokens remain valid.

Only remove volumes when intentionally resetting the environment:

```powershell
docker compose --env-file .env.cloud down -v
```
