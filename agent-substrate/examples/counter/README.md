# Counter actor example

This intentionally tiny actor demonstrates the Agent Substrate contract. It stores its logical state under `SUBSTRATE_WORKSPACE` (normally the mounted `/workspace` PVC), persists updates atomically, and deduplicates the most recent 4,096 `requestId` values. The controller supplies a per-Agent bearer credential through the Kubernetes Secret reference `SUBSTRATE_ACTOR_TOKEN`; `/invoke` and `/checkpoint` reject requests without it.

Build it from the repository root with:

```sh
docker build -t REGISTRY/OWNER/agent-substrate-counter:0.1.0 \
  -f agent-substrate/examples/counter/Dockerfile agent-substrate/examples/counter
```

Update the image in `agent.yaml` to the pushed image, preferably pinned by digest, before applying it. Supported requests:

```json
{"op":"increment","by":3,"requestId":"job-123"}
```

The response includes the persisted `value` and whether the request was a duplicate. `POST /checkpoint` fsyncs the state file and workspace directory before returning. This is a sample contract, not a general-purpose agent runtime.
