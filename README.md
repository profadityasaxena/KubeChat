# KubeChat

KubeChat is a minimal end-to-end **Docker + Kubernetes** practice project: a simple web chatbot that runs **Llama** in a container, reads user-uploaded files from a separate **file-store** container, and answers questions by combining model output with retrieved file content (RAG-lite). All services are dockerized for local development and orchestrated with Kubernetes for cluster practice.

> **Goal:** Provide a clean, reproducible playground to exercise day-to-day Docker and `kubectl` commands while seeing a working app.

---

## Architecture

### Services (Containers)

- **ui**  
    Minimal web UI (Next.js or static SPA) to chat with the backend.
- **api**  
    Lightweight API gateway (FastAPI/Node) that orchestrates:
    - **llama** — Llama runtime (e.g., `llama.cpp` or Ollama) served over HTTP.
    - **files-api** — Reads files from a mounted volume/PVC and exposes a simple `/files` and `/chunks` API for retrieval.
- **vector-db (optional)**  
    Embeddings store (e.g., Qdrant) if you want to extend to full RAG.

**Data flow (happy path):**  
`ui → api → (llama + files-api) → api → ui`

---

## Features

- One-command local bring-up with Docker Compose.
- Kubernetes manifests for Deployments, Services, Ingress, ConfigMaps, PVCs.
- Pluggable Llama backend (llama.cpp or Ollama; model pulled at startup).
- File-store backed by a Docker Volume (locally) or PVC (in-cluster).
- Clear, commented YAML + make targets to practice common ops.

---

## Repo Layout

```
kubechat/
├─ docker/
│   ├─ api.Dockerfile
│   ├─ ui.Dockerfile
│   ├─ files-api.Dockerfile
│   └─ llama.Dockerfile # or use upstream image via .env
├─ compose.yaml # local dev stack
├─ k8s/ # production-like manifests (base)
│   ├─ namespace.yaml
│   ├─ configmap.yaml # API/LLM config
│   ├─ secret.example.yaml
│   ├─ deployments/
│   │   ├─ api.yaml
│   │   ├─ ui.yaml
│   │   ├─ files-api.yaml
│   │   └─ llama.yaml
│   ├─ services/
│   │   ├─ api.yaml
│   │   ├─ ui.yaml
│   │   ├─ files-api.yaml
│   │   └─ llama.yaml
│   ├─ ingress.yaml # optional (Nginx/Traefik)
│   └─ storage.yaml # PVCs/StorageClass (dev)
├─ src/
│   ├─ api/
│   ├─ ui/
│   └─ files-api/
├─ .env.example
├─ Makefile
└─ README.md
```

---

## Prerequisites

- **Docker** (Desktop or Engine 24+)
- **kubectl** (v1.28+)
- **kind** or **minikube** for a local cluster
- **Helm** (optional) if you prefer chart-based installs

---

## Quickstart

### Local (Docker)

```bash
# 1) Clone and configure
git clone https://github.com/<you>/kubechat.git
cd kubechat
cp .env.example .env
# set LLM backend: LLAMA_IMAGE, MODEL_ID or path, API keys if any

# 2) Bring up the stack
docker compose up -d --build

# 3) Verify containers
docker ps
docker logs -f kubechat-api

# 4) Open the app
# UI default: http://localhost:3000
# API default: http://localhost:8000/healthz
# Files mount: ./data/  -> files-api container at /data
```

### Kubernetes (kind/minikube)

```bash
# 1) Create cluster (kind example)
kind create cluster --name kubechat

# 2) Namespace & secrets
kubectl apply -f k8s/namespace.yaml
# Edit secrets before applying
kubectl apply -f k8s/secret.example.yaml

# 3) Storage and config
kubectl apply -f k8s/storage.yaml
kubectl apply -f k8s/configmap.yaml

# 4) Core services
kubectl apply -f k8s/deployments/
kubectl apply -f k8s/services/

# 5) (Optional) Ingress
kubectl apply -f k8s/ingress.yaml

# 6) Verify
kubectl get pods -n kubechat -o wide
kubectl get svc -n kubechat
kubectl logs -n kubechat deploy/api

# NodePort (if configured)
kubectl get svc -n kubechat

# Port-forward (simple)
kubectl -n kubechat port-forward deploy/ui 3000:3000
kubectl -n kubechat port-forward deploy/api 8000:8000
```

---

## Configuration

Key `.env` variables (used by Compose and injected via ConfigMap/Secret in k8s):

- **LLAMA_IMAGE** — e.g., `ghcr.io/ggerganov/llama.cpp:latest` or `ollama/ollama:latest`
- **MODEL_ID** — model name/path (e.g., `llama3.1:8b` for Ollama)
- **API_PORT**, **UI_PORT**, **FILES_PORT**
- **FILES_PATH** — host path for local dev (mounted to `/data` in files-api)
- **CHUNK_SIZE**, **TOP_K**, **TEMPERATURE** — basic inference/RAG knobs
# KubeChat
