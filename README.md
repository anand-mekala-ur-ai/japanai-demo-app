# JapanAI Demo App

## Requirements

- Node.js
- npm or pnpm
- Python 3.10+
- uv

## Setup

### Environment Files

Copy the example env files to `.env`:

```bash
cp frontend/env.txt frontend/.env
cp backend/env.txt backend/.env
```

### Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

### Backend

```bash
cd backend
uv venv
uv sync
uv run uvicorn main:app --reload
```