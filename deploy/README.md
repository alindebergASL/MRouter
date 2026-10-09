# deploy/

Everything needed to run Purser: images, Compose stacks, the Helm chart, AWS infrastructure, and
native service units. Architecture §9 sets the packaging rules: containers by default, native only
where a container costs the user something real.

| Directory | Contents |
|---|---|
| `images/` | One Dockerfile per image |
| `compose/` | `dev.yaml` (the build's stack); later `sidecar.yaml` |
| `helm/` | Chart for the pod sidecar; relay and control plane later |
| `aws/` | Infrastructure as code for us-east-1 (from Phase 2) |
| `native/` | systemd, launchd, and Windows service units; installers |
