from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Dict, Optional
from uuid import uuid4


class UploadJobManager:
    def __init__(self) -> None:
        # Keep uploads serialized to avoid resource spikes from many PDFs at once.
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()

    def create_job(self, file_name: str) -> Dict[str, Any]:
        job_id = str(uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        job = {
            "job_id": job_id,
            "status": "queued",
            "file_name": file_name,
            "message": "Upload queued for background processing.",
            "stage": "queued",
            "document_id": None,
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job_id] = job
        return dict(job)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def submit(
        self,
        job_id: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any
    ) -> None:
        self._executor.submit(self._run_job, job_id, fn, *args, **kwargs)

    def progress(self, job_id: str, stage: str, message: str) -> None:
        """Update job with a granular processing stage."""
        self.update(job_id, stage=stage, message=message)

    def _run_job(
        self,
        job_id: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any
    ) -> None:
        self.update(job_id, status="processing", stage="starting", message="Processing upload...")
        try:
            def on_progress(stage: str, message: str) -> None:
                self.progress(job_id, stage, message)
            result = fn(*args, on_progress=on_progress, **kwargs)
            result_dict = result.model_dump() if hasattr(result, "model_dump") else result
            self.update(
                job_id,
                status="completed",
                message="Upload processing completed.",
                document_id=result_dict.get("document_id"),
                result=result_dict,
                error=None,
            )
        except Exception as exc:
            self.update(
                job_id,
                status="failed",
                message="Upload processing failed.",
                error=str(exc),
            )

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(fields)
            job["updated_at"] = datetime.utcnow().isoformat() + "Z"


_upload_job_manager = UploadJobManager()


def get_upload_job_manager() -> UploadJobManager:
    return _upload_job_manager
