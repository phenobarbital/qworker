# Configuring Health Checks for QWorker Pods with AWS ALB

This guide explains how to configure Kubernetes readiness and liveness probes for QWorker pods so that the **AWS Load Balancer Controller** (ALB Ingress) automatically stops routing traffic to pods whose internal task queue is full.

---

## Table of Contents

- [Overview](#overview)
- [How QWorker Health Checks Work](#how-qworker-health-checks-work)
  - [Endpoints](#endpoints)
  - [Readiness Logic (Queue-Aware)](#readiness-logic-queue-aware)
  - [Response Format](#response-format)
- [Configuration Reference](#configuration-reference)
  - [Environment Variables](#environment-variables)
  - [CLI Flags](#cli-flags)
- [Kubernetes Deployment Configuration](#kubernetes-deployment-configuration)
  - [Pod Spec with Probes](#pod-spec-with-probes)
  - [Full Deployment Manifest](#full-deployment-manifest)
- [AWS ALB Integration](#aws-alb-integration)
  - [Ingress Resource](#ingress-resource)
  - [TargetGroupBinding (Advanced)](#targetgroupbinding-advanced)
  - [How ALB Uses the Readiness Probe](#how-alb-uses-the-readiness-probe)
- [Dynamic Queue Sizing and Health](#dynamic-queue-sizing-and-health)
  - [Growth and Shrink Behavior](#growth-and-shrink-behavior)
  - [Ceiling Calculation](#ceiling-calculation)
  - [How Readiness Tracks Queue Pressure](#how-readiness-tracks-queue-pressure)
- [Architecture Diagram](#architecture-diagram)
- [Tuning Guide](#tuning-guide)
  - [Probe Timing](#probe-timing)
  - [Queue Sizing for Your Workload](#queue-sizing-for-your-workload)
  - [Scaling Integration](#scaling-integration)
- [Troubleshooting](#troubleshooting)
- [Examples](#examples)
  - [Verifying Health from Inside a Pod](#verifying-health-from-inside-a-pod)
  - [Monitoring with Prometheus](#monitoring-with-prometheus)

---

## Overview

QWorker ships a built-in HTTP health server that exposes queue state on a configurable port (default `8080`). Kubernetes uses these endpoints as **readiness** and **liveness probes**. When combined with the AWS Load Balancer Controller, the ALB stops sending new tasks to any pod whose queue has reached its ceiling capacity, distributing load to healthier pods instead.

Key behavior:

| Condition | Readiness Response | ALB Action |
|-----------|-------------------|------------|
| Queue has capacity | `200 OK` | Routes traffic to pod |
| Queue at ceiling | `503 Service Unavailable` | Stops routing to pod |
| Process alive | Liveness `200 OK` | Keeps pod running |
| Process dead | Liveness timeout | Restarts pod |

---

## How QWorker Health Checks Work

### Endpoints

The health server exposes three HTTP endpoints:

| Endpoint | Method | Purpose | Probe Type |
|----------|--------|---------|------------|
| `GET /health/ready` | GET | Returns `200` if queue has capacity, `503` if at ceiling | Readiness |
| `GET /health` | GET | Alias for `/health/ready` | Readiness |
| `GET /health/live` | GET | Always returns `200` (process is alive) | Liveness |

### Readiness Logic (Queue-Aware)

The readiness probe is **not** a simple "process is running" check. It inspects the internal task queue and returns `503` only when the queue reaches its **ceiling** (base size + grow margin):

```
ceiling = base_size + grow_margin
```

- **`200 OK`** — `queue.size < ceiling` — the pod can accept more work.
- **`503 Service Unavailable`** — `queue.size >= ceiling` — the pod is saturated; the ALB should stop routing to it.

This means a queue that is "full" at its base size still returns `200` as long as the grow margin has room. The pod only becomes unready when all capacity (including growth buffer) is exhausted.

### Response Format

**Readiness (`/health/ready`):**

```json
{
  "status": "ok",
  "queue": {
    "size": 2,
    "max_size": 4,
    "base_size": 4,
    "grow_margin": 2,
    "grow_events": 0,
    "discard_events": 0,
    "full": false
  },
  "worker": "Worker"
}
```

When the queue is at ceiling:

```json
{
  "status": "full",
  "queue": {
    "size": 6,
    "max_size": 6,
    "base_size": 4,
    "grow_margin": 2,
    "grow_events": 2,
    "discard_events": 1,
    "full": true
  },
  "worker": "Worker"
}
```

**Liveness (`/health/live`):**

```json
{
  "status": "alive",
  "worker": "Worker"
}
```

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKER_HEALTH_ENABLED` | `true` | Enable/disable the health HTTP server |
| `WORKER_HEALTH_PORT` | `8080` | TCP port for health endpoints |
| `WORKER_QUEUE_SIZE` | `4` | Base queue capacity per worker |
| `WORKER_QUEUE_GROW_MARGIN` | `2` | Extra slots above base before returning 503 |
| `WORKER_QUEUE_WARN_THRESHOLD` | `0.80` | Fraction of capacity that triggers a warning log |
| `WORKER_QUEUE_SHRINK_COOLDOWN` | `30` | Seconds of low usage before shrinking queue back |

### CLI Flags

```bash
qw start \
  --host 0.0.0.0 \
  --port 8888 \
  --workers 4 \
  --queue 4 \
  --health-port 8080
```

| Flag | Default | Maps to |
|------|---------|---------|
| `--health-port` | `8080` | `WORKER_HEALTH_PORT` |
| `--queue` | `4` | `WORKER_QUEUE_SIZE` |

> **Note:** Only worker 0 in each process group starts the health server to avoid port conflicts when running multiple workers per pod.

---

## Kubernetes Deployment Configuration

### Pod Spec with Probes

```yaml
containers:
  - name: qworker
    image: your-registry/qworker:2.0.4
    ports:
      - name: worker
        containerPort: 8888
        protocol: TCP
      - name: health
        containerPort: 8080
        protocol: TCP
    env:
      - name: WORKER_HEALTH_ENABLED
        value: "true"
      - name: WORKER_HEALTH_PORT
        value: "8080"
      - name: WORKER_QUEUE_SIZE
        value: "4"
      - name: WORKER_QUEUE_GROW_MARGIN
        value: "2"
    readinessProbe:
      httpGet:
        path: /health/ready
        port: 8080
      initialDelaySeconds: 5
      periodSeconds: 10
      timeoutSeconds: 3
      successThreshold: 1
      failureThreshold: 3
    livenessProbe:
      httpGet:
        path: /health/live
        port: 8080
      initialDelaySeconds: 10
      periodSeconds: 15
      timeoutSeconds: 3
      failureThreshold: 5
```

### Full Deployment Manifest

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qworker
  namespace: workers
  labels:
    app: qworker
    component: task-worker
spec:
  replicas: 3
  selector:
    matchLabels:
      app: qworker
  template:
    metadata:
      labels:
        app: qworker
        component: task-worker
    spec:
      terminationGracePeriodSeconds: 30
      containers:
        - name: qworker
          image: your-registry/qworker:2.0.4
          command: ["qw", "start"]
          args:
            - "--host=0.0.0.0"
            - "--port=8888"
            - "--workers=4"
            - "--queue=4"
            - "--health-port=8080"
          ports:
            - name: worker
              containerPort: 8888
              protocol: TCP
            - name: health
              containerPort: 8080
              protocol: TCP
          env:
            - name: WORKER_HEALTH_ENABLED
              value: "true"
            - name: WORKER_HEALTH_PORT
              value: "8080"
            - name: WORKER_QUEUE_SIZE
              value: "4"
            - name: WORKER_QUEUE_GROW_MARGIN
              value: "2"
            - name: WORKER_QUEUE_SHRINK_COOLDOWN
              value: "30"
            - name: REDIS_HOST
              valueFrom:
                secretKeyRef:
                  name: qworker-secrets
                  key: redis-host
            - name: REDIS_PORT
              value: "6379"
          readinessProbe:
            httpGet:
              path: /health/ready
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 3
            successThreshold: 1
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health/live
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 15
            timeoutSeconds: 3
            failureThreshold: 5
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 512Mi
---
apiVersion: v1
kind: Service
metadata:
  name: qworker
  namespace: workers
  labels:
    app: qworker
spec:
  type: ClusterIP
  ports:
    - name: worker
      port: 8888
      targetPort: worker
      protocol: TCP
    - name: health
      port: 8080
      targetPort: health
      protocol: TCP
  selector:
    app: qworker
```

---

## AWS ALB Integration

### Ingress Resource

The AWS Load Balancer Controller creates an ALB from an `Ingress` resource. The ALB uses the pod's readiness gate (backed by the readiness probe) to decide whether to route traffic to each target.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: qworker-ingress
  namespace: workers
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip

    # Health check configuration — ALB polls these directly
    alb.ingress.kubernetes.io/healthcheck-path: /health/ready
    alb.ingress.kubernetes.io/healthcheck-port: "8080"
    alb.ingress.kubernetes.io/healthcheck-protocol: HTTP
    alb.ingress.kubernetes.io/healthcheck-interval-seconds: "10"
    alb.ingress.kubernetes.io/healthcheck-timeout-seconds: "5"
    alb.ingress.kubernetes.io/healthy-threshold-count: "2"
    alb.ingress.kubernetes.io/unhealthy-threshold-count: "3"
    alb.ingress.kubernetes.io/success-codes: "200"

    # Enable pod readiness gate so ALB waits for probe before routing
    alb.ingress.kubernetes.io/target-group-attributes: >-
      deregistration_delay.timeout_seconds=30
spec:
  ingressClassName: alb
  rules:
    - host: qworker.internal.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: qworker
                port:
                  number: 8888
```

### TargetGroupBinding (Advanced)

For finer control over ALB target groups, use `TargetGroupBinding` directly:

```yaml
apiVersion: elbv2.k8s.aws/v1beta1
kind: TargetGroupBinding
metadata:
  name: qworker-tgb
  namespace: workers
spec:
  serviceRef:
    name: qworker
    port: 8888
  targetGroupARN: arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/qworker-tg/abc123
  targetType: ip
  networking:
    ingress:
      - from:
          - securityGroup:
              groupID: sg-xxxxxxxxxxxx
        ports:
          - port: 8080
            protocol: TCP
```

### How ALB Uses the Readiness Probe

The flow works as follows:

1. **Pod starts** — QWorker initializes, worker 0 starts the health server on port 8080.
2. **Kubelet probes** `/health/ready` — once it returns `200`, the pod enters `Ready` state.
3. **ALB registers target** — the AWS Load Balancer Controller adds the pod IP to the target group.
4. **ALB health checks** — the ALB independently polls `/health/ready` on port 8080.
5. **Queue fills up** — when `queue.size >= ceiling`, `/health/ready` returns `503`.
6. **ALB deregisters target** — after `unhealthy-threshold-count` consecutive 503s, the ALB stops routing new requests to this pod.
7. **Queue drains** — existing tasks complete, queue size drops below ceiling.
8. **Readiness recovers** — `/health/ready` returns `200` again.
9. **ALB re-registers** — after `healthy-threshold-count` consecutive 200s, the ALB resumes routing.

```
Client Request
      │
      ▼
┌──────────┐     health check: GET /health/ready:8080
│  AWS ALB │────────────────────────────────────────────┐
│          │                                            │
└────┬─────┘                                            │
     │ routes only to healthy targets                   │
     │                                                  │
     ├──────────────────┐──────────────────┐            │
     ▼                  ▼                  ▼            │
┌─────────┐      ┌─────────┐       ┌─────────┐         │
│  Pod A  │      │  Pod B  │       │  Pod C  │         │
│ 200 OK  │      │ 200 OK  │       │ 503 FULL│ ◄───────┘
│ queue:  │      │ queue:  │       │ queue:  │   ALB stops
│ 2/6     │      │ 3/6     │       │ 6/6     │   routing here
└─────────┘      └─────────┘       └─────────┘
```

---

## Dynamic Queue Sizing and Health

### Growth and Shrink Behavior

QWorker uses a **dynamic queue sizing policy** that allows the queue to grow beyond its base size under pressure, up to a hard ceiling:

```
base_size = 4    (WORKER_QUEUE_SIZE)
grow_margin = 2  (WORKER_QUEUE_GROW_MARGIN)
ceiling = 6      (base_size + grow_margin)
```

| Queue Size | State | Readiness | ALB Action |
|-----------|-------|-----------|------------|
| 0–3 | Normal | `200 OK` | Routes traffic |
| 4 | Full at base | `200 OK` | Routes traffic (grow margin available) |
| 5 | Growing | `200 OK` | Routes traffic |
| 6 | At ceiling | `503 Full` | Stops routing |

### Ceiling Calculation

```
ceiling = WORKER_QUEUE_SIZE + WORKER_QUEUE_GROW_MARGIN
```

The ceiling is the hard limit. Once the queue reaches this size, no more tasks can be accepted and the readiness probe returns `503`.

### How Readiness Tracks Queue Pressure

The readiness check uses the `snapshot()` method from `QueueManager`, which provides a real-time view of:

| Field | Description |
|-------|-------------|
| `size` | Current number of tasks in queue |
| `max_size` | Current effective maximum (may be between base and ceiling) |
| `base_size` | Configured baseline capacity |
| `grow_margin` | Extra slots allowed above base |
| `grow_events` | Number of times the queue has grown |
| `discard_events` | Number of tasks rejected at ceiling |
| `full` | Whether the underlying asyncio.Queue reports full |

The decision is simple: `size >= ceiling` → `503`. Everything else → `200`.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Kubernetes Cluster                       │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              AWS ALB (Load Balancer Controller)        │  │
│  │                                                       │  │
│  │  Health Check Config:                                 │  │
│  │    Path: /health/ready                                │  │
│  │    Port: 8080                                         │  │
│  │    Interval: 10s                                      │  │
│  │    Unhealthy threshold: 3                             │  │
│  │    Healthy threshold: 2                               │  │
│  └───────┬──────────────┬──────────────┬─────────────────┘  │
│          │              │              │                     │
│          ▼              ▼              ▼                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│  │   Pod A      │ │   Pod B      │ │   Pod C      │        │
│  │              │ │              │ │              │         │
│  │  ┌────────┐  │ │  ┌────────┐  │ │  ┌────────┐  │        │
│  │  │QWorker │  │ │  │QWorker │  │ │  │QWorker │  │        │
│  │  │Server  │  │ │  │Server  │  │ │  │Server  │  │        │
│  │  │:8888   │  │ │  │:8888   │  │ │  │:8888   │  │        │
│  │  └───┬────┘  │ │  └───┬────┘  │ │  └───┬────┘  │        │
│  │      │       │ │      │       │ │      │       │        │
│  │  ┌───▼────┐  │ │  ┌───▼────┐  │ │  ┌───▼────┐  │        │
│  │  │Health  │  │ │  │Health  │  │ │  │Health  │  │        │
│  │  │Server  │  │ │  │Server  │  │ │  │Server  │  │        │
│  │  │:8080   │  │ │  │:8080   │  │ │  │:8080   │  │        │
│  │  └────────┘  │ │  └────────┘  │ │  └────────┘  │        │
│  │              │ │              │ │              │         │
│  │  Queue: 2/6  │ │  Queue: 4/6  │ │  Queue: 6/6  │        │
│  │  Ready: ✓    │ │  Ready: ✓    │ │  Ready: ✗    │        │
│  └──────────────┘ └──────────────┘ └──────────────┘        │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                   Redis (Transport)                    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Tuning Guide

### Probe Timing

| Parameter | Recommended | Why |
|-----------|-------------|-----|
| `initialDelaySeconds` (readiness) | `5` | QWorker starts the health server early in boot |
| `periodSeconds` (readiness) | `10` | Frequent enough to catch queue saturation quickly |
| `timeoutSeconds` | `3` | Health server responds in <1ms; generous for network jitter |
| `failureThreshold` (readiness) | `3` | 3 failures × 10s = 30s before pod is removed from ALB |
| `successThreshold` (readiness) | `1` | Re-add pod as soon as queue has capacity |
| `periodSeconds` (liveness) | `15` | Less aggressive than readiness; only kills truly dead pods |
| `failureThreshold` (liveness) | `5` | 5 × 15s = 75s before restart; avoids killing slow startups |

**ALB-specific timing:**

| Annotation | Recommended | Why |
|------------|-------------|-----|
| `healthcheck-interval-seconds` | `10` | Match readiness periodSeconds |
| `unhealthy-threshold-count` | `3` | Deregister after 30s of 503s |
| `healthy-threshold-count` | `2` | Re-register after 20s of 200s |
| `deregistration_delay.timeout_seconds` | `30` | Allow in-flight tasks to complete |

### Queue Sizing for Your Workload

| Workload Type | `WORKER_QUEUE_SIZE` | `WORKER_QUEUE_GROW_MARGIN` | Rationale |
|---------------|--------------------|-----------------------------|-----------|
| Fast tasks (<1s) | `8` | `4` | High throughput, large buffer |
| Medium tasks (1-10s) | `4` | `2` | Balanced (default) |
| Slow tasks (10-60s) | `2` | `1` | Small queue, fast saturation signal |
| Long-running (>60s) | `1` | `1` | One at a time, immediate backpressure |

### Scaling Integration

Combine health checks with Horizontal Pod Autoscaler (HPA) for automatic scaling:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: qworker-hpa
  namespace: workers
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: qworker
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Pods
      pods:
        metric:
          name: qworker_queue_size
        target:
          type: AverageValue
          averageValue: "3"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30
      policies:
        - type: Pods
          value: 2
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120
```

When used together:

1. **Readiness probe** provides immediate backpressure — ALB stops routing to full pods.
2. **HPA** provides capacity scaling — adds more pods when average queue size is high.
3. **Deregistration delay** ensures graceful draining — in-flight tasks complete before removal.

---

## Troubleshooting

### Pod never becomes Ready

```bash
# Check if health server started
kubectl logs <pod-name> -n workers | grep "Health server"

# Expected output:
# Health server listening on 0.0.0.0:8080

# Test from inside the pod
kubectl exec -it <pod-name> -n workers -- \
  curl -s http://localhost:8080/health/ready | python3 -m json.tool
```

**Common causes:**
- `WORKER_HEALTH_ENABLED` set to `false`
- Port 8080 already in use (another container or sidecar)
- Health server only starts on worker 0; if multiprocessing spawns workers with non-zero IDs in a way that skips 0, the health server won't start

### Pod flapping between Ready and Not Ready

```bash
# Check queue state over time
watch -n 2 "kubectl exec <pod-name> -n workers -- \
  curl -s http://localhost:8080/health/ready"
```

**Common causes:**
- `WORKER_QUEUE_SIZE` too small for the workload
- `WORKER_QUEUE_GROW_MARGIN` is 0 (no buffer)
- Tasks are slow, causing the queue to oscillate around ceiling
- **Fix:** Increase `WORKER_QUEUE_SIZE` or `WORKER_QUEUE_GROW_MARGIN`

### ALB health check fails but pod probe passes

The ALB health check runs independently from kubelet probes. Verify:

```bash
# Check ALB target health
aws elbv2 describe-target-health \
  --target-group-arn <target-group-arn>

# Verify security group allows ALB to reach port 8080
aws ec2 describe-security-groups \
  --group-ids <pod-security-group-id>
```

**Common causes:**
- Security group does not allow inbound on port 8080 from ALB
- ALB health check path differs from probe path (check annotations)
- `target-type: ip` requires the ALB to reach pod IPs directly

### Health server not starting

Check the worker logs for:

```
Could not start health server on port 8080: [Errno 98] Address already in use
```

**Fix:** Change `WORKER_HEALTH_PORT` to an unused port, or ensure no sidecar/init container binds to 8080.

### Discard events increasing

```bash
kubectl exec <pod-name> -n workers -- \
  curl -s http://localhost:8080/health/ready | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f'Discards: {data[\"queue\"][\"discard_events\"]}')
print(f'Grow events: {data[\"queue\"][\"grow_events\"]}')
print(f'Queue: {data[\"queue\"][\"size\"]}/{data[\"queue\"][\"max_size\"]}')
"
```

Increasing `discard_events` means tasks are being rejected because the queue is at ceiling. Either scale out (more pods) or increase queue capacity.

---

## Examples

### Verifying Health from Inside a Pod

```bash
# Readiness check
kubectl exec -it <pod-name> -n workers -- \
  curl -v http://localhost:8080/health/ready

# Liveness check
kubectl exec -it <pod-name> -n workers -- \
  curl -v http://localhost:8080/health/live

# Watch queue state in real time
kubectl exec -it <pod-name> -n workers -- \
  sh -c 'while true; do curl -s http://localhost:8080/health/ready; echo; sleep 2; done'
```

### Verifying ALB Target Health

```bash
# List all targets and their health status
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/qworker-tg/abc123 \
  --query 'TargetHealthDescriptions[].{Target:Target.Id,Port:Target.Port,State:TargetHealth.State,Reason:TargetHealth.Reason}' \
  --output table
```

### Monitoring with Prometheus

Scrape the health endpoint to create Prometheus metrics:

```yaml
# prometheus-config.yaml (scrape config)
scrape_configs:
  - job_name: qworker-health
    metrics_path: /health/ready
    scrape_interval: 10s
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: qworker
        action: keep
      - source_labels: [__meta_kubernetes_pod_ip]
        target_label: __address__
        replacement: "${1}:8080"
```

Since the health endpoint returns JSON (not Prometheus format), use a **json_exporter** or build a lightweight sidecar:

```yaml
# Sidecar that converts /health/ready JSON into Prometheus metrics
containers:
  - name: health-exporter
    image: your-registry/qworker-health-exporter:latest
    ports:
      - name: metrics
        containerPort: 9090
    env:
      - name: HEALTH_URL
        value: "http://localhost:8080/health/ready"
```

Or use a `PodMonitor` with the json_exporter:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: qworker-health
  namespace: workers
spec:
  selector:
    matchLabels:
      app: qworker
  podMetricsEndpoints:
    - port: metrics
      interval: 10s
      path: /metrics
```

### Dockerfile Snippet

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install qworker

EXPOSE 8888 8080

ENTRYPOINT ["qw", "start", "--host=0.0.0.0", "--port=8888", "--health-port=8080"]
```
