# ruff: noqa: B008
from __future__ import annotations

import os
import re
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from fallguard import __version__
from fallguard.config import AppConfig, load_config

app = typer.Typer(
    name="fallguard",
    help="Pose-sequence fall detection: data, training, inference, API, and dashboard.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
data_app = typer.Typer(help="Download, prepare, and validate datasets.", no_args_is_help=True)
infer_app = typer.Typer(help="Run trained-model inference.", no_args_is_help=True)
telegram_app = typer.Typer(
    help="Configure and verify Telegram fall alerts.",
    no_args_is_help=True,
)
app.add_typer(data_app, name="data")
app.add_typer(infer_app, name="infer")
app.add_typer(telegram_app, name="telegram")


def _configure_utf8_console() -> None:
    """Keep Vietnamese CLI messages safe on redirected/legacy Windows shells."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue


_configure_utf8_console()
console = Console()
CONFIG_HELP = (
    "YAML config. Defaults to FALLGUARD_CONFIG, then ./configs/mvp.yaml, "
    "then built-in MVP settings."
)


class DatasetSelection(StrEnum):
    smoke = "smoke"
    limit = "limit"
    full = "full"


class SourceProfile(StrEnum):
    preview = "preview"
    rgb = "rgb"


class PoseVariant(StrEnum):
    lite = "lite"
    full = "full"
    heavy = "heavy"


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"fallguard-ai {__version__}")
        raise typer.Exit


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed FallGuard version.",
    ),
) -> None:
    """FallGuard AI command line."""


@app.command()
def doctor(
    config: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help=CONFIG_HELP
    ),
) -> None:
    """Check dependencies, accelerator, configuration, and storage."""

    from fallguard.doctor import doctor_is_healthy, run_doctor

    results = run_doctor(config)
    table = Table(title="FallGuard environment")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for result in results:
        status = "[green]PASS[/green]" if result.ok else (
            "[yellow]OPTIONAL[/yellow]" if not result.required else "[red]FAIL[/red]"
        )
        table.add_row(result.name, status, result.detail)
    console.print(table)
    if not doctor_is_healthy(results):
        raise typer.Exit(1)


@data_app.command("download-urfd")
def download_urfd_command(
    output: Path = typer.Option(Path("data/raw/urfd"), help="Raw URFD destination."),
    selection: DatasetSelection = typer.Option(
        DatasetSelection.smoke,
        help="smoke=2 sequences, limit=N balanced prefix, full=70 sequences.",
    ),
    profile: SourceProfile = typer.Option(
        SourceProfile.preview,
        help="preview=small official MP4; rgb=full-resolution PNG ZIPs.",
    ),
    limit: int | None = typer.Option(None, min=1, max=70),
    accept_license: bool = typer.Option(
        False,
        "--accept-license",
        help="Acknowledge URFD CC BY-NC-SA 4.0 academic/non-commercial terms.",
    ),
    extract: bool = typer.Option(True, "--extract/--no-extract"),
    keep_archives: bool = typer.Option(True, "--keep-archives/--remove-archives"),
) -> None:
    """Download official URFD camera-0 data with resume and integrity checks."""

    from fallguard.data.urfd import LicenseNotAccepted, download_urfd

    if selection is DatasetSelection.limit and limit is None:
        console.print("[red]--selection limit requires --limit N[/red]")
        raise typer.Exit(2)
    if selection is not DatasetSelection.limit and limit is not None:
        console.print("[red]--limit is only valid with --selection limit[/red]")
        raise typer.Exit(2)
    try:
        with console.status("Downloading and validating official URFD assets..."):
            report = download_urfd(
                output,
                accept_license=accept_license,
                selection=selection.value,
                limit=limit,
                profile=profile.value,
                extract=extract,
                keep_archives=keep_archives,
            )
    except LicenseNotAccepted as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        f"[green]URFD ready:[/green] {len(report.sequence_ids)} sequences, "
        f"{_human_bytes(report.downloaded_bytes)} transferred this run, "
        f"profile={report.profile}, root={report.root}"
    )


@data_app.command("synthetic")
def synthetic_command(
    output: Path = typer.Option(Path("data/processed/synthetic")),
    sequences: int = typer.Option(18, min=6),
    frames: int = typer.Option(100, min=40),
    seed: int = typer.Option(42, min=0),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Generate a deterministic offline dataset and training windows."""

    from fallguard.data.synthetic import generate_synthetic_dataset

    report = generate_synthetic_dataset(
        output,
        num_sequences=sequences,
        frames_per_sequence=frames,
        seed=seed,
        overwrite=overwrite,
    )
    console.print(f"[green]Synthetic dataset ready:[/green] {report.manifest_path}")
    if report.window_report is not None:
        for item in report.window_report.files:
            console.print(
                f"  {item.split:10s} {item.num_windows:5d} windows "
                f"[{item.window_size}, {item.input_size}] -> {item.path}"
            )


@data_app.command("prepare-urfd")
def prepare_urfd_command(
    config: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help=CONFIG_HELP
    ),
    raw_root: Path | None = typer.Option(None, help="Defaults to <raw_dir>/urfd."),
    source: SourceProfile = typer.Option(SourceProfile.preview),
    pose_variant: PoseVariant | None = typer.Option(None),
    limit: int | None = typer.Option(None, min=1, max=70),
    overwrite: bool = typer.Option(False, "--overwrite"),
    materialize: bool = typer.Option(True, "--materialize/--poses-only"),
) -> None:
    """Extract MediaPipe poses, split by sequence, and create causal windows."""

    from fallguard.data.manifest import build_manifest, write_manifest
    from fallguard.data.urfd import discover_urfd_sequences, select_urfd_sequences
    from fallguard.features.materialize import materialize_window_datasets
    from fallguard.pose.mediapipe_estimator import (
        MediaPipePoseEstimator,
        download_pose_model,
    )
    from fallguard.pose.prepare import prepare_sequences

    settings = load_config(config)
    dataset_root = raw_root or settings.paths.raw_dir / "urfd"
    variant = (pose_variant.value if pose_variant else settings.data.pose_model_variant)
    model_path = Path("data/models") / f"pose_landmarker_{variant}.task"
    if not model_path.is_file():
        console.print(f"Downloading MediaPipe {variant} pose model...")
        download_pose_model(model_path.parent, variant=variant)

    records = discover_urfd_sequences(dataset_root, prefer=source.value)
    if limit is not None:
        selected = select_urfd_sequences("limit", limit=limit)
        by_id = {record.sequence_id: record for record in records}
        records = [by_id[item] for item in selected if item in by_id]
    if not records:
        console.print(
            f"[red]No URFD {source.value} sequences found under {dataset_root}.[/red]"
        )
        raise typer.Exit(1)

    pose_dir = settings.paths.interim_dir / f"urfd_{source.value}" / "poses"

    def estimator_factory() -> MediaPipePoseEstimator:
        return MediaPipePoseEstimator(
            model_path,
            min_visibility=settings.data.min_pose_visibility,
            min_pose_detection_confidence=settings.data.min_pose_detection_confidence,
            min_pose_presence_confidence=settings.data.min_pose_presence_confidence,
            min_tracking_confidence=settings.data.min_pose_tracking_confidence,
        )

    with console.status(
        f"Extracting {len(records)} pose sequences at {settings.data.target_fps} FPS..."
    ):
        preparation = prepare_sequences(
            records,
            pose_dir,
            estimator_factory,
            target_fps=float(settings.data.target_fps),
            overwrite=overwrite,
        )
    detected = sum(item.detected_frames for item in preparation)
    frames = sum(item.num_frames for item in preparation)
    console.print(
        f"[green]Pose extraction ready:[/green] {len(preparation)} sequences, "
        f"{detected}/{frames} frames detected ({detected / max(frames, 1):.1%})."
    )

    if not materialize:
        return
    if len(records) < 6:
        console.print(
            "[yellow]Fewer than six sequences cannot form robust train/validation/test "
            "splits; poses were saved but windows were not materialized.[/yellow]"
        )
        return

    absolute_records = [
        replace(
            record,
            path=record.path.resolve(),
            annotation_path=(
                record.annotation_path.resolve() if record.annotation_path else None
            ),
        )
        for record in records
    ]
    ratios = settings.data.splits.model_dump()
    entries = build_manifest(
        absolute_records,
        ratios=ratios,
        seed=settings.project.seed,
        stratify=True,
    )
    manifest_path = (
        settings.paths.manifest_dir / f"urfd_{source.value}_sequences.csv"
    )
    write_manifest(entries, manifest_path, relative_to=manifest_path.parent)
    processed_dir = settings.paths.processed_dir / f"urfd_{source.value}"
    window_report = materialize_window_datasets(
        entries,
        processed_dir,
        pose_dir=pose_dir,
        window_size=settings.data.window_size,
        stride=settings.data.stride,
        max_interpolation_gap=settings.data.max_interpolation_gap,
        include_xyz=settings.features.include_xyz,
        include_visibility=settings.features.include_visibility,
        include_velocity=settings.features.include_velocity,
        include_geometry=settings.features.include_geometry,
    )
    console.print(f"[green]Training data ready:[/green] {processed_dir}")
    for item in window_report.files:
        console.print(
            f"  {item.split:10s} {item.num_windows:5d} windows "
            f"[{item.window_size}, {item.input_size}]"
        )


@data_app.command("materialize")
def materialize_command(
    manifest: Path = typer.Argument(..., exists=True, dir_okay=False),
    pose_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output: Path = typer.Option(Path("data/processed/urfd")),
    config: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help=CONFIG_HELP
    ),
) -> None:
    """Create training-ready window NPZ files from prepared pose archives."""

    from fallguard.features.materialize import materialize_window_datasets

    settings = load_config(config)
    report = materialize_window_datasets(
        manifest,
        output,
        pose_dir=pose_dir,
        window_size=settings.data.window_size,
        stride=settings.data.stride,
        max_interpolation_gap=settings.data.max_interpolation_gap,
        include_xyz=settings.features.include_xyz,
        include_visibility=settings.features.include_visibility,
        include_velocity=settings.features.include_velocity,
        include_geometry=settings.features.include_geometry,
    )
    for item in report.files:
        console.print(
            f"[green]{item.split}[/green]: {item.num_windows} windows -> {item.path}"
        )


@data_app.command("validate")
def validate_data_command(
    pose_dir: Path = typer.Option(Path("data/interim/urfd_preview/poses")),
    processed_dir: Path = typer.Option(Path("data/processed/urfd_preview")),
) -> None:
    """Validate pose NPZ contracts and consolidated window shapes."""

    import numpy as np

    from fallguard.pose.prepare import validate_pose_npz
    from fallguard.training.data import load_window_dataset

    pose_paths = sorted(pose_dir.glob("*.npz")) if pose_dir.is_dir() else []
    failures: list[str] = []
    total_pose_frames = 0
    detected_frames = 0
    for path in pose_paths:
        try:
            info = validate_pose_npz(path)
            total_pose_frames += info.num_frames
            with np.load(path, allow_pickle=False) as archive:
                detected_frames += int(archive["valid"].any(axis=1).sum())
        except ValueError as exc:
            failures.append(str(exc))

    table = Table(title="Prepared data")
    table.add_column("Part")
    table.add_column("Items")
    table.add_column("Shape / quality")
    table.add_row(
        "poses",
        str(len(pose_paths)),
        (
            f"{detected_frames}/{total_pose_frames} detected "
            f"({detected_frames / max(total_pose_frames, 1):.1%})"
        ),
    )
    for split in ("train", "validation", "test"):
        try:
            dataset = load_window_dataset(processed_dir, split=split)
            table.add_row(
                split,
                str(len(dataset)),
                f"[{dataset.window_size}, {dataset.input_size}]",
            )
        except (FileNotFoundError, ValueError) as exc:
            failures.append(f"{split}: {exc}")
            table.add_row(split, "missing/invalid", "-")
    console.print(table)
    if failures:
        for failure in failures:
            console.print(f"[red]{failure}[/red]")
        raise typer.Exit(1)


@app.command()
def train(
    config: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help=CONFIG_HELP
    ),
    data_dir: Path | None = typer.Option(None, help="Directory with windows_*.npz."),
    output: Path | None = typer.Option(None, help="Artifact run directory."),
    epochs: int | None = typer.Option(None, min=1),
    device: str | None = typer.Option(None, help="auto, cpu, cuda, or cuda:N."),
) -> None:
    """Train a deterministic multi-task GRU/LSTM/TCN and save all artifacts."""

    from fallguard.training.trainer import train_model

    settings = load_config(config)
    if data_dir is not None:
        settings.paths.processed_dir = data_dir
    if epochs is not None:
        settings.training.epochs = epochs
    if device is not None:
        settings.project.device = device  # validated by the runtime resolver
    run_dir = output or (
        settings.paths.artifact_dir
        / f"{settings.data.dataset}-{settings.model.architecture}-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    result = train_model(settings, output_dir=run_dir)
    console.print(
        f"[green]Training complete:[/green] {result.epochs_completed} epochs, "
        f"best epoch {result.best_epoch}"
    )
    console.print(f"Checkpoint: {result.best_checkpoint}")
    _print_metric_summary(result.metrics)


@app.command()
def evaluate(
    checkpoint: Path = typer.Argument(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(Path("data/processed/urfd_preview")),
    split: str = typer.Option("test"),
    threshold: float = typer.Option(0.5, min=0.0, max=1.0),
    calibration: Path | None = typer.Option(
        None,
        exists=True,
        dir_okay=False,
        help="Use a validation-only calibration.json threshold.",
    ),
    output: Path | None = typer.Option(None),
    device: str = typer.Option("auto"),
) -> None:
    """Evaluate a checkpoint on a held-out split and write honest metrics."""

    from fallguard.training.calibration import load_calibrated_threshold
    from fallguard.training.evaluate import evaluate_checkpoint

    output_path = output or checkpoint.parent / f"{split}_metrics.json"
    resolved_threshold = (
        load_calibrated_threshold(calibration) if calibration is not None else threshold
    )
    metrics = evaluate_checkpoint(
        checkpoint,
        data_dir,
        split=split,
        device=device,
        event_threshold=resolved_threshold,
        output_path=output_path,
    )
    _print_metric_summary(metrics)
    console.print(f"Metrics: {output_path}")


@app.command()
def calibrate(
    checkpoint: Path = typer.Argument(..., exists=True, dir_okay=False),
    validation_data: Path = typer.Option(
        Path("data/processed/urfd_preview"),
        help="Validation dataset directory; test-labelled paths are rejected.",
    ),
    objective: str = typer.Option("event_f1", help="event_f1 or frame_f1."),
    method: str = typer.Option("grid", help="grid or pr_curve."),
    output: Path | None = typer.Option(None),
    device: str = typer.Option("auto"),
) -> None:
    """Tune the event threshold using validation data only."""

    from fallguard.training.calibration import calibrate_event_threshold

    result = calibrate_event_threshold(
        checkpoint,
        validation_data,
        objective=objective,
        method=method,
        output_path=output,
        device=device,
    )
    chosen = result["chosen_metrics"]
    console.print(
        f"[green]Validation calibration complete:[/green] "
        f"threshold={result['chosen_threshold']:.3f}, "
        f"{result['objective']}={result['objective_score']:.3f}"
    )
    console.print(
        f"Frame precision/recall/F1: {chosen['frame']['precision']:.3f} / "
        f"{chosen['frame']['recall']:.3f} / {chosen['frame']['f1']:.3f}"
    )
    console.print(f"Calibration: {result['calibration_path']}")


@infer_app.command("video")
def infer_video_command(
    input_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    checkpoint: Path | None = typer.Option(None, help="Defaults to the local trained MVP."),
    config: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help=CONFIG_HELP
    ),
    pose_model: Path | None = typer.Option(None),
    output: Path | None = typer.Option(None),
    events: Path | None = typer.Option(None),
    urfd_preview: bool = typer.Option(False, "--urfd-preview"),
    max_seconds: float = typer.Option(300.0, min=1.0),
) -> None:
    """Run pose + temporal model + state machine on a video."""

    from fallguard.inference.pipeline import InferencePipeline
    from fallguard.inference.state_machine import FallStateMachine

    settings = load_config(config)
    predictor, estimator, adapter = _runtime_components(
        settings, checkpoint=checkpoint, pose_model=pose_model
    )
    result_dir = settings.paths.runtime_dir / "results"
    output_path = output or result_dir / f"{input_path.stem}-annotated.mp4"
    events_path = events or result_dir / f"{input_path.stem}-events.json"
    pipeline = InferencePipeline(
        classifier=predictor,
        pose_estimator=estimator,
        state_machine=FallStateMachine(settings.event_detection),
        window_size=settings.data.window_size,
        stride=settings.data.stride,
        window_adapter=adapter,
    )
    try:
        result = pipeline.run_video(
            input_path,
            output_video=output_path,
            events_json=events_path,
            target_fps=float(settings.data.target_fps),
            max_duration_seconds=max_seconds,
            crop_right_half=urfd_preview,
        )
    finally:
        estimator.close()
    console.print(
        f"[green]Inference complete:[/green] {result.prediction_count} predictions, "
        f"{len(result.events)} confirmed/recovered events, "
        f"{result.processing_fps:.1f} processing FPS"
    )
    console.print(f"Video:  {result.output_video}")
    console.print(f"Events: {result.events_json}")


@telegram_app.command("discover")
def telegram_discover_command() -> None:
    """Find Chat IDs from recent messages sent to the configured bot."""

    from fallguard.notifications import TelegramNotifier

    notifier = TelegramNotifier.from_env()
    try:
        chats = notifier.discover_recent_chats()
    except (RuntimeError, ValueError) as exc:
        console.print(f"[red]Không thể tìm Chat ID:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not chats:
        console.print(
            "[yellow]Chưa tìm thấy cuộc trò chuyện nào.[/yellow] "
            "Mở bot Telegram, nhấn Start hoặc gửi [bold]/start[/bold], "
            "sau đó chạy lại lệnh này."
        )
        return

    table = Table(title="Telegram chats gần đây")
    table.add_column("Chat ID")
    table.add_column("Loại")
    table.add_column("Tên")
    table.add_column("Username")
    for chat in chats:
        username = f"@{chat.username}" if chat.username else "-"
        table.add_row(chat.chat_id, chat.chat_type, chat.title, username)
    console.print(table)
    console.print(
        "Đặt Chat ID cần nhận cảnh báo vào "
        "[bold]FALLGUARD_TELEGRAM_CHAT_ID[/bold]."
    )


@telegram_app.command("test")
def telegram_test_command(
    snapshot: Path | None = typer.Option(
        None,
        "--snapshot",
        "--photo",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Optional JPEG evidence image to include in the test alert.",
    ),
) -> None:
    """Send a safe sample alert using credentials from environment variables."""

    from fallguard.notifications import TelegramNotifier

    notifier = TelegramNotifier.from_env()
    if not notifier.enabled:
        console.print(
            "[red]Telegram chưa được cấu hình.[/red] Hãy đặt "
            "FALLGUARD_TELEGRAM_BOT_TOKEN và FALLGUARD_TELEGRAM_CHAT_ID "
            "(không truyền token trên dòng lệnh)."
        )
        raise typer.Exit(2)

    event = {
        "event_id": "FALLGUARD-TELEGRAM-TEST",
        "state": "CONFIRMED_FALL",
        "risk_level": "HIGH",
        "timestamp_ms": 0,
        "occurred_at": datetime.now().astimezone().isoformat(),
        "camera_id": "CAM-TEST",
        "person_id": "DEMO",
        "fall_probability": 0.99,
        "inactive_seconds": 8.0,
        "metadata": {
            "posture": "LYING",
            "test_notification": True,
        },
    }
    result = notifier.notify(event, snapshot_jpeg=snapshot)
    if result.success:
        console.print(
            f"[green]Đã gửi cảnh báo Telegram thử nghiệm tới "
            f"{result.sent_count} cuộc trò chuyện.[/green]"
        )
        return

    console.print(
        f"[red]Gửi thử thất bại:[/red] {result.failed_count} lỗi, "
        f"{result.sent_count} thành công."
    )
    for delivery in result.deliveries:
        if not delivery.success:
            status = (
                f"HTTP {delivery.status_code}"
                if delivery.status_code is not None
                else "lỗi kết nối"
            )
            console.print(f"  Chat {delivery.chat_id}: {status} — {delivery.error or 'unknown'}")
    raise typer.Exit(1)


@infer_app.command("camera")
def infer_camera_command(
    source: int = typer.Option(0, min=0),
    checkpoint: Path | None = typer.Option(None),
    config: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help=CONFIG_HELP
    ),
    pose_model: Path | None = typer.Option(None),
    camera_id: str | None = typer.Option(
        None,
        help="Camera label stored with confirmed events (default: CAM-LIVE-<source>).",
    ),
    person_id: str | None = typer.Option(
        None,
        help="Optional monitored person identifier.",
    ),
    telegram_alerts: bool = typer.Option(
        True,
        "--telegram/--no-telegram",
        help="Send confirmed-fall snapshots when Telegram environment variables are set.",
    ),
) -> None:
    """Run live webcam inference, persist falls, and send optional Telegram alerts."""

    import cv2

    from fallguard.api.store import SQLiteEventStore
    from fallguard.inference.evidence import save_snapshot
    from fallguard.inference.pipeline import InferencePipeline, event_to_dict
    from fallguard.inference.state_machine import FallStateMachine
    from fallguard.notifications import TelegramNotifier

    settings = load_config(config)
    predictor, estimator, adapter = _runtime_components(
        settings, checkpoint=checkpoint, pose_model=pose_model
    )
    pipeline = InferencePipeline(
        classifier=predictor,
        pose_estimator=estimator,
        state_machine=FallStateMachine(settings.event_detection),
        window_size=settings.data.window_size,
        stride=settings.data.stride,
        window_adapter=adapter,
    )
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        estimator.close()
        raise typer.BadParameter(f"Could not open camera {source}")

    resolved_camera_id = camera_id or f"CAM-LIVE-{source}"
    event_store = SQLiteEventStore(settings.paths.runtime_dir / "fallguard.db")
    snapshot_dir = settings.paths.runtime_dir / "results" / "snapshots"
    notifier = TelegramNotifier.from_env()
    telegram_enabled = telegram_alerts and notifier.enabled
    notification_executor = (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="fallguard-telegram")
        if telegram_enabled
        else None
    )
    handled_event_ids: set[str] = set()
    interval = 1000.0 / settings.data.target_fps
    next_sample_ms = 0.0
    started = time.perf_counter()
    frame_index = 0
    console.print("Live inference started. Press Q or Escape in the camera window to stop.")
    if telegram_alerts and not notifier.enabled:
        console.print(
            "[yellow]Telegram chưa được cấu hình; ảnh và sự kiện vẫn được lưu cục bộ.[/yellow]"
        )
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            timestamp_ms = int((time.perf_counter() - started) * 1000)
            result = None
            if timestamp_ms >= next_sample_ms:
                result = pipeline.process_frame(frame, frame_index, timestamp_ms)
                next_sample_ms += interval
            canvas = pipeline.annotate_frame(frame, result)
            if result is not None and result.event is not None:
                event = result.event
                event_id = str(event.event_id or "").strip()
                just_confirmed = bool(event.metadata.get("just_confirmed"))
                if just_confirmed and event_id and event_id not in handled_event_ids:
                    handled_event_ids.add(event_id)
                    safe_name = _safe_filename_component(event_id)
                    snapshot_path = snapshot_dir / f"{safe_name}.jpg"
                    saved_snapshot: Path | None = None
                    try:
                        saved_snapshot = save_snapshot(canvas, snapshot_path)
                    except (OSError, RuntimeError, TypeError, ValueError, cv2.error) as exc:
                        console.print(
                            f"[yellow]Không thể lưu ảnh sự kiện {event_id}: {exc}[/yellow]"
                        )

                    record = event_to_dict(event)
                    metadata = dict(record.get("metadata") or {})
                    metadata.update(
                        {
                            "camera_id": resolved_camera_id,
                            "person_id": person_id,
                            "source": f"camera:{source}",
                            "snapshot_path": (
                                str(saved_snapshot.resolve())
                                if saved_snapshot is not None
                                else None
                            ),
                            "captured_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    record.update(
                        {
                            "camera_id": resolved_camera_id,
                            "person_id": person_id,
                            "source": f"camera:{source}",
                            "fall_probability": (
                                float(result.prediction.fall_probability)
                                if result.prediction is not None
                                else float(
                                    metadata.get("smoothed_fall_probability", 0.0)
                                )
                            ),
                            "metadata": metadata,
                        }
                    )
                    try:
                        stored_event = event_store.upsert_event(record)
                    except (OSError, RuntimeError, ValueError) as exc:
                        stored_event = record
                        console.print(
                            f"[red]Không thể lưu sự kiện {event_id} vào SQLite: {exc}[/red]"
                        )
                    else:
                        console.print(
                            f"[bold red]ĐÃ XÁC NHẬN TÉ NGÃ[/bold red] — {event_id}"
                        )
                        if saved_snapshot is not None:
                            console.print(f"Ảnh bằng chứng: {saved_snapshot}")

                    if notification_executor is not None:
                        notification_executor.submit(
                            _send_live_telegram_alert,
                            notifier,
                            stored_event,
                            saved_snapshot,
                        )
                        console.print("Đã xếp hàng gửi cảnh báo Telegram.")
            cv2.imshow("FallGuard AI", canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in {27, ord("q"), ord("Q")}:
                break
            frame_index += 1
    finally:
        capture.release()
        cv2.destroyAllWindows()
        estimator.close()
        if notification_executor is not None:
            notification_executor.shutdown(wait=True)
        event_store.close()


@app.command()
def serve(
    config: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help=CONFIG_HELP
    ),
    checkpoint: Path | None = typer.Option(None),
    pose_model: Path | None = typer.Option(None),
    host: str | None = typer.Option(None),
    port: int | None = typer.Option(None, min=1, max=65535),
    open_browser: bool = typer.Option(False, "--open"),
) -> None:
    """Start FastAPI, SQLite event history, alerts, and the dashboard."""

    import uvicorn

    from fallguard.api.main import create_app

    settings = load_config(config)
    if checkpoint is not None:
        os.environ["FALLGUARD_MODEL_PATH"] = str(checkpoint.resolve())
    elif _default_checkpoint().is_file():
        os.environ["FALLGUARD_MODEL_PATH"] = str(_default_checkpoint().resolve())
    if pose_model is not None:
        os.environ["FALLGUARD_POSE_MODEL"] = str(pose_model.resolve())
    application = create_app(settings)
    bind_host = host or settings.api.host
    bind_port = port or settings.api.port
    url = f"http://{bind_host}:{bind_port}"
    console.print(f"[green]FallGuard dashboard:[/green] {url}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(application, host=bind_host, port=bind_port)


@app.command()
def demo(
    output: Path = typer.Option(Path("artifacts/demo")),
    epochs: int = typer.Option(3, min=1),
    config: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help=CONFIG_HELP
    ),
) -> None:
    """Run an offline synthetic data-to-train-to-evaluate smoke workflow."""

    from fallguard.data.synthetic import generate_synthetic_dataset
    from fallguard.training.evaluate import evaluate_checkpoint
    from fallguard.training.trainer import train_model

    synthetic_root = Path("data/processed/demo")
    report = generate_synthetic_dataset(
        synthetic_root,
        num_sequences=18,
        frames_per_sequence=100,
        seed=42,
        overwrite=True,
    )
    settings = load_config(config)
    settings.paths.processed_dir = synthetic_root
    settings.training.epochs = epochs
    settings.training.patience = max(1, min(settings.training.patience, epochs))
    training = train_model(settings, output_dir=output)
    test_metrics = evaluate_checkpoint(
        training.best_checkpoint,
        synthetic_root,
        split="test",
        output_path=output / "test_metrics.json",
    )
    console.print(
        f"[green]Offline demo complete:[/green] {len(report.entries)} sequences, "
        f"checkpoint={training.best_checkpoint}"
    )
    _print_metric_summary(test_metrics)


def _runtime_components(
    settings: AppConfig,
    *,
    checkpoint: Path | None,
    pose_model: Path | None,
) -> tuple[Any, Any, Any]:
    from fallguard.inference.pipeline import pose_window_to_features
    from fallguard.models.predictor import TemporalPredictor
    from fallguard.pose.mediapipe_estimator import (
        MediaPipePoseEstimator,
        download_pose_model,
    )

    checkpoint_path = checkpoint or _default_checkpoint()
    if not checkpoint_path.is_file():
        raise typer.BadParameter(
            "No trained checkpoint found. Pass --checkpoint or run `fallguard train`."
        )
    model_path = pose_model or (
        Path("data/models")
        / f"pose_landmarker_{settings.data.pose_model_variant}.task"
    )
    if not model_path.is_file():
        download_pose_model(
            model_path.parent,
            variant=settings.data.pose_model_variant,
        )
    predictor = TemporalPredictor.from_checkpoint(
        checkpoint_path,
        device=settings.project.device,
    )
    estimator = MediaPipePoseEstimator(
        model_path,
        min_visibility=settings.data.min_pose_visibility,
        min_pose_detection_confidence=settings.data.min_pose_detection_confidence,
        min_pose_presence_confidence=settings.data.min_pose_presence_confidence,
        min_tracking_confidence=settings.data.min_pose_tracking_confidence,
    )
    adapter = partial(
        pose_window_to_features,
        max_interpolation_gap=settings.data.max_interpolation_gap,
        include_xyz=settings.features.include_xyz,
        include_visibility=settings.features.include_visibility,
        include_velocity=settings.features.include_velocity,
        include_geometry=settings.features.include_geometry,
    )
    return predictor, estimator, adapter


def _default_checkpoint() -> Path:
    return Path("artifacts/urfd_preview_gru/best.pt")


def _safe_filename_component(value: str) -> str:
    """Return a path-traversal-safe component for locally generated evidence."""

    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return normalized[:120] or "fall-event"


def _send_live_telegram_alert(
    notifier: Any,
    event: dict[str, Any],
    snapshot_path: Path | None,
) -> None:
    """Send from the camera worker without propagating errors into capture."""

    try:
        result = notifier.notify(event, snapshot_jpeg=snapshot_path)
    except Exception:  # pragma: no cover - defensive boundary for third-party clients
        console.print("[red]Không thể gửi cảnh báo Telegram (lỗi không mong đợi).[/red]")
        return
    if result.success:
        console.print(
            f"[green]Đã gửi cảnh báo Telegram tới {result.sent_count} cuộc trò chuyện.[/green]"
        )
    else:
        console.print(
            f"[red]Cảnh báo Telegram thất bại cho {result.failed_count} "
            "cuộc trò chuyện.[/red]"
        )


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def _print_metric_summary(metrics: dict[str, Any]) -> None:
    posture = metrics.get("posture", {})
    event = metrics.get("event", {})
    level = metrics.get("event_level", {})
    table = Table(title="Evaluation summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    if "accuracy" in posture:
        table.add_row("Posture accuracy", f"{float(posture['accuracy']):.3f}")
    if "macro_f1" in posture:
        table.add_row("Posture macro F1", f"{float(posture['macro_f1']):.3f}")
    for label, key in (
        ("Event precision", "precision"),
        ("Event recall", "recall"),
        ("Event F1", "f1"),
    ):
        if key in event:
            table.add_row(label, f"{float(event[key]):.3f}")
    if level.get("available"):
        table.add_row("Event-level F1", f"{float(level['f1']):.3f}")
        table.add_row(
            "False alarms / video",
            f"{float(level['false_alarms_per_video']):.3f}",
        )
    console.print(table)


if __name__ == "__main__":
    app()
