# Agent Substrate — Kubernetes lifecycle MVP

**Agent Substrate** is a small, runnable control plane for isolated, stateful AI actors on Kubernetes. This first cut turns the idea in the design reference into working primitives: an `Agent` custom resource, a namespace-scoped controller, a bearer-authenticated gateway, per-actor Pods with a persistent workspace PVC, checkpoint-before-suspend, and request-triggered wake-up.

> **Scope honesty:** this is a single-controller MVP, not a production platform for millions of agents. It creates one Pod per active actor; it does **not** yet pool warm worker Pods or provide Firecracker/microVM isolation. It requires an installed gVisor or Kata `RuntimeClass` for the advertised container sandbox boundary. It persists files on a PVC and asks the actor to checkpoint logical state; it cannot snapshot arbitrary process memory. Do not use it with untrusted tenants until you have reviewed and tested the cluster runtime, CNI, storage, ingress/authentication, and operational controls.

## What is implemented

- `Agent` CRD with desired state (`Running` / `Sleeping`), OCI image, RuntimeClass, workspace size and idle timeout.
- A stdlib-only Python controller/gateway that reconciles namespaced Agents through the Kubernetes API.
- One restricted Pod per active actor: non-root UID, read-only root filesystem, dropped Linux capabilities, `RuntimeDefault` seccomp, no service-account token, bounded CPU/memory, and an explicit sandbox RuntimeClass.
- One persistent `ReadWriteOnce` PVC per actor. The controller calls `POST /checkpoint` before deleting a Pod. If checkpointing fails, it **keeps the Pod running** rather than discard the actor's live state.
- Idle suspension and wake-on-`invoke`; actor state remains on the PVC across Pod recreation.
- A namespace NetworkPolicy that denies actor egress and only permits the control plane to reach actors on port 8080. The cluster CNI must enforce NetworkPolicy.
- A counter example with atomic durable writes and bounded request-ID deduplication.
- Local CLI commands for status, suspend, resume and invoke.

## Requirements

- Kubernetes 1.27+ with CRDs, a dynamic default `StorageClass`, and a CNI that enforces `NetworkPolicy`.
- An installed `RuntimeClass` named `gvisor` (or change each Agent's `runtimeClassName` to an installed Kata/gVisor class). The controller does not install a runtime handler on nodes.
- Docker/BuildKit and a registry reachable by the cluster, for the controller and counter images.
- `kubectl` access to create the namespace/CRD and to apply Agent resources.

Check the cluster before deploying:

```sh
kubectl get storageclass
kubectl get runtimeclass gvisor
kubectl get nodes
```

If `gvisor` is absent, install and validate the runtime handler on every eligible node first; alternatively use a configured Kata RuntimeClass. Setting `runtimeClassName` to an ordinary container runtime is **not** equivalent isolation.

## Build and deploy

From the repository root, build and push both images. Replace `REGISTRY/OWNER` with a registry your cluster can pull from:

```sh
docker build -t REGISTRY/OWNER/agent-substrate:0.1.0 \
  -f agent-substrate/Dockerfile agent-substrate
docker push REGISTRY/OWNER/agent-substrate:0.1.0

docker build -t REGISTRY/OWNER/agent-substrate-counter:0.1.0 \
  -f agent-substrate/examples/counter/Dockerfile agent-substrate/examples/counter
docker push REGISTRY/OWNER/agent-substrate-counter:0.1.0
```

Edit `agent-substrate/manifests/controller.yaml` to use the first image, and `agent-substrate/examples/counter/agent.yaml` to use the counter image (prefer an immutable `@sha256:...` digest in real clusters).

Create the namespace and the gateway token, then install the controller resources:

```sh
kubectl apply -f agent-substrate/manifests/namespace.yaml
API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
ACTOR_MASTER="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
kubectl -n agent-substrate create secret generic agent-substrate-api \
  --from-literal=token="$API_TOKEN" --from-literal=actor-master="$ACTOR_MASTER"
unset API_TOKEN ACTOR_MASTER
kubectl apply -k agent-substrate/manifests
kubectl wait -n agent-substrate --for=condition=Available deployment/agent-substrate-controller --timeout=120s
```

Wait for the CRD to become established, then create the example actor:

```sh
kubectl wait --for=condition=Established crd/agents.substrate.ai --timeout=60s
kubectl apply -f agent-substrate/examples/counter/agent.yaml
kubectl -n agent-substrate get agents
```

The example manifest defaults to `gvisor`. Its image reference is a placeholder until you edit it as described above.

## Try it locally

Use a loopback-only port-forward. The gateway token is read from the Kubernetes Secret into the shell environment; do not paste it into source control or chat.

```sh
kubectl -n agent-substrate port-forward svc/agent-substrate 8080:8080
```

In a second terminal:

```sh
export SUBSTRATE_GATEWAY_URL=http://127.0.0.1:8080
export SUBSTRATE_API_TOKEN="$(kubectl -n agent-substrate get secret agent-substrate-api \
  -o jsonpath='{.data.token}' | base64 -d)"

python3 agent-substrate/substrate/cli.py status counter
python3 agent-substrate/substrate/cli.py invoke counter --by 3 --request-id demo-001
python3 agent-substrate/substrate/cli.py suspend counter
# The next invoke wakes the Pod, waits for readiness, then proxies the request.
python3 agent-substrate/substrate/cli.py invoke counter --by 2 --request-id demo-002
python3 agent-substrate/substrate/cli.py status counter
unset SUBSTRATE_API_TOKEN
```

The counter should report `5` after the wake. Its state file is on the actor's PVC, not in the container's writable layer. The `requestId` is required so callers can safely retry the counter example; the sample remembers the most recent 4,096 IDs.

## Gateway API

The Service is `ClusterIP` only. The example port-forward binds to loopback; there is no public Ingress in this MVP.

- `GET /healthz` — liveness; no actor data.
- `GET /readyz` — readiness against the Kubernetes API.
- `GET /v1/agents/{name}` — status; bearer token required.
- `POST /v1/agents/{name}/invoke` — wakes if needed, then proxies JSON to the actor's `/invoke` endpoint.
- `POST /v1/agents/{name}/suspend` — requests checkpoint and suspension.
- `POST /v1/agents/{name}/resume` — requests resume; the controller starts the Pod asynchronously.

All `/v1` routes require `Authorization: Bearer $SUBSTRATE_API_TOKEN`. Requests and actor responses are limited to 64 KiB. The gateway accepts only actor names, not caller-provided URLs, and forwards only the fixed `/invoke` and `/checkpoint` paths to Kubernetes-assigned Pod IPs.

## Actor contract

An actor image must:

1. Listen on `0.0.0.0:8080`.
2. Return success from `GET /healthz` only when it can accept work.
3. Accept `POST /invoke` with a JSON object and return a JSON object. The controller injects a per-Agent bearer credential as `SUBSTRATE_ACTOR_TOKEN`; require it on mutation endpoints.
4. Accept authenticated `POST /checkpoint` and durably flush all **logical** state needed to continue from `/workspace` before returning 2xx.
5. Treat retries safely. The gateway does not promise exactly-once delivery; the example uses a `requestId` dedupe window.

The persistent workspace is mounted at `/workspace`. Keep transient caches under `/tmp` (memory-backed, 64 MiB). Root filesystem is read-only. On Agent deletion, Kubernetes garbage-collects the owned PVC too; export any state you need before deleting the CR.

## Security boundaries and current limitations

- **Isolation:** one Pod per actor and a configured gVisor/Kata RuntimeClass. The software cannot prove that node handlers or the CNI are configured correctly. Verify them independently.
- **Network:** actor egress is denied by the supplied NetworkPolicy. Model/provider access and external callbacks are intentionally not implemented; a future egress broker is required. A CNI that ignores NetworkPolicy invalidates this control.
- **Credentials:** one shared gateway bearer token is a bootstrap mechanism, not multi-tenant identity. A separate control-plane-only master key derives a different HMAC credential for each Kubernetes Agent UID; the controller stores it in an owner-scoped immutable Kubernetes Secret, and the actor receives it through a Secret reference rather than in its Pod spec. Keep the master key stable; automatic actor-token rotation is not implemented. Keep the Service internal and use a properly authenticated TLS ingress before remote use. Actor Pods receive no Kubernetes token and no provider secrets.
- **State:** the PVC preserves files, not RAM. The checkpoint endpoint is an actor contract and must be tested for each runtime. A failed checkpoint leaves the Pod up and reports `CheckpointFailed`.
- **Scale/availability:** the controller currently polls all Agents every two seconds, runs one replica, and has no leader election, queue, sharding, warm worker pool, billing, per-tenant quotas, or HA. It is a small-cluster MVP, not evidence for “millions of agents.”
- **Supply chain:** pin actor images by digest, scan/sign them, restrict registry access, and set cluster admission policy before production.

## Development and tests

The control plane and tests use only Python's standard library:

```sh
python3 -m unittest discover -s agent-substrate/tests -v
python3 -m compileall -q agent-substrate/substrate agent-substrate/examples/counter
```

No live Kubernetes cluster is required for unit tests. A passing unit suite proves the local controller/gateway contracts, not a production cluster, sandbox runtime, or NetworkPolicy enforcement.
