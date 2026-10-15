"""Tests for the audio delivery service."""

import json
from pathlib import Path

import pytest

from core.analysis_schemas import (
    AnalysisDocument,
    AudioDeliveryStatus,
    AudioFitMode,
    ContentBlock,
    MediaInfo,
)
from services.audio_delivery_service import AudioDeliveryService


@pytest.fixture
def sample_doc(tmp_path: Path) -> AnalysisDocument:
    """Create a minimal AnalysisDocument with two blocks for testing."""
    media = MediaInfo(
        duration_ms=10000,
        fps_num=30,
        fps_den=1,
        width=1920,
        height=1080,
        audio_sample_rate=44100,
        audio_channels=2,
    )
    block1 = ContentBlock(
        block_id="blk_1",
        start_ms=0,
        end_ms=5000,
        source_start_ms=0,
        source_end_ms=5000,
        transcript="Hello world",
    )
    block2 = ContentBlock(
        block_id="blk_2",
        start_ms=5000,
        end_ms=10000,
        source_start_ms=5000,
        source_end_ms=10000,
        transcript="Goodbye world",
    )
    doc = AnalysisDocument(
        schema_version=1,
        analysis_id="test-analysis-id",
        project_id="proj_test",
        media=media,
        speakers=[],
        shots=[],
        transcript_segments=[],
        audio_events=[],
        content_blocks=[block1, block2],
        review={},
        metadata={},
    )
    # Persist to disk so service can load it
    proj_dir = tmp_path / "proj_test" / "block_analysis"
    proj_dir.mkdir(parents=True, exist_ok=True)
    doc_path = proj_dir / "document.json"
    doc_path.write_text(json.dumps(doc.model_dump(mode="json")), encoding="utf-8")
    return doc


class TestAudioDeliveryService:
    def test_upload_audio_creates_delivery(self, tmp_path: Path, sample_doc: AnalysisDocument):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        updated = svc.upload_audio(
            project_id="proj_test",
            block_id="blk_1",
            file_path=str(tmp_path / "fake.wav"),
            actual_duration_ms=4800,
            fit_mode="preserve_speed",
        )
        blk = updated.get_block("blk_1")
        assert blk is not None
        assert blk.audio_delivery is not None
        assert blk.audio_delivery.actual_duration_ms == 4800
        assert blk.audio_delivery.status == AudioDeliveryStatus.UPLOADED
        assert blk.audio_delivery.approved is False

    def test_validate_audio_fit_within_tolerance(self, tmp_path: Path, sample_doc: AnalysisDocument):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        svc.upload_audio(
            project_id="proj_test",
            block_id="blk_1",
            file_path=str(tmp_path / "fake.wav"),
            actual_duration_ms=5100,  # expected 5000, delta +100 <= 200 tolerance
        )
        result = svc.validate_audio_fit("proj_test", "blk_1")
        assert result["ok"] is True
        assert abs(result["delta_ms"]) <= 200

    def test_validate_audio_outside_tolerance(self, tmp_path: Path, sample_doc: AnalysisDocument):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        svc.upload_audio(
            project_id="proj_test",
            block_id="blk_1",
            file_path=str(tmp_path / "fake.wav"),
            actual_duration_ms=6000,  # expected 5000, delta +1000 > 200
        )
        result = svc.validate_audio_fit("proj_test", "blk_1")
        assert result["ok"] is False
        assert result["delta_ms"] == 1000

    def test_approve_and_revoke(self, tmp_path: Path, sample_doc: AnalysisDocument):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        svc.upload_audio(
            project_id="proj_test",
            block_id="blk_1",
            file_path=str(tmp_path / "fake.wav"),
            actual_duration_ms=5000,
        )
        approved = svc.approve_audio("proj_test", "blk_1")
        blk = approved.get_block("blk_1")
        assert blk.audio_delivery.approved is True
        assert blk.audio_delivery.status == AudioDeliveryStatus.APPROVED

        revoked = svc.revoke_approval("proj_test", "blk_1")
        blk2 = revoked.get_block("blk_1")
        assert blk2.audio_delivery.approved is False
        assert blk2.audio_delivery.status == AudioDeliveryStatus.UPLOADED

    def test_get_summary_counts_correctly(self, tmp_path: Path, sample_doc: AnalysisDocument):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        # Initially no deliveries
        summary = svc.get_summary("proj_test")
        assert summary["total_blocks"] == 2
        assert summary["delivered"] == 0
        assert summary["approved"] == 0
        assert summary["all_ready"] is False

        # Upload one, approve it
        svc.upload_audio("proj_test", "blk_1", str(tmp_path / "a.wav"), 5000)
        svc.approve_audio("proj_test", "blk_1")
        summary2 = svc.get_summary("proj_test")
        assert summary2["delivered"] == 1
        assert summary2["approved"] == 1
        assert summary2["pending"] == 1
        assert summary2["all_ready"] is False

        # Upload second, don't approve
        svc.upload_audio("proj_test", "blk_2", str(tmp_path / "b.wav"), 5000)
        summary3 = svc.get_summary("proj_test")
        assert summary3["delivered"] == 2
        assert summary3["approved"] == 1
        assert summary3["pending"] == 1

        # Approve second
        svc.approve_audio("proj_test", "blk_2")
        summary4 = svc.get_summary("proj_test")
        assert summary4["approved"] == 2
        assert summary4["all_ready"] is True

    def test_gap_overlap_recalculation(self, tmp_path: Path, sample_doc: AnalysisDocument):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        # Both blocks have audio; check gap/overlap fields get populated
        svc.upload_audio("proj_test", "blk_1", str(tmp_path / "a.wav"), 5000)
        svc.upload_audio("proj_test", "blk_2", str(tmp_path / "b.wav"), 5000)
        doc = svc.get_document("proj_test")
        b1 = doc.get_block("blk_1")
        b2 = doc.get_block("blk_2")
        # blk_1 ends at 5000, blk_2 starts at 5000 → gap=0, overlap=0
        assert b1.audio_delivery.gap_ms == 0
        assert b1.audio_delivery.overlap_ms == 0
        assert b2.audio_delivery.gap_ms == 0
        assert b2.audio_delivery.overlap_ms == 0


# ======================================================================
# Phase C: block-based audio delivery API
# ======================================================================

import struct
import wave


def _make_wav(path: Path, duration_ms: int, sample_rate: int = 8000) -> Path:
    """Generate a real WAV file (sine-ish tone) with the given duration."""
    n_samples = int(sample_rate * duration_ms / 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = b"".join(
            struct.pack("<h", int(12000 * (i % 32) / 32)) for i in range(n_samples)
        )
        wf.writeframes(frames)
    return path


class _FakeUpload:
    """Mimics a Streamlit UploadedFile."""

    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass


@pytest.fixture
def phase_c_doc(tmp_path: Path) -> Path:
    """Persist a two-block document; blocks are 0-5000 and 5000-10000 ms."""
    media = MediaInfo(
        duration_ms=10000, fps_num=30, fps_den=1,
        width=1920, height=1080, audio_sample_rate=44100, audio_channels=2,
    )
    blocks = [
        ContentBlock(
            block_id=f"blk_{i}", start_ms=(i - 1) * 5000, end_ms=i * 5000,
            source_start_ms=(i - 1) * 5000, source_end_ms=i * 5000,
            transcript=f"segment {i}",
        )
        for i in (1, 2)
    ]
    doc = AnalysisDocument(
        schema_version=1, analysis_id="pc-id", project_id="proj_test",
        media=media, speakers=[], shots=[], transcript_segments=[],
        audio_events=[], content_blocks=blocks, review={}, metadata={},
    )
    proj_dir = tmp_path / "proj_test" / "block_analysis"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "document.json").write_text(
        json.dumps(doc.model_dump(mode="json")), encoding="utf-8"
    )
    return tmp_path


class TestPhaseCAudioDelivery:
    def test_upload_wav_and_probe_duration(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src" / "clip.wav", 5000)
        doc = svc.upload_block_audio("proj_test", "blk_1", str(wav))
        d = doc.get_block("blk_1").audio_delivery
        assert d is not None
        # Probed duration ≈ 5000 ms (FFprobe, not client-supplied)
        assert abs(d.actual_duration_ms - 5000) < 100
        assert d.sample_rate == 8000
        assert d.channels == 1
        assert d.codec == "pcm_s16le"
        assert d.content_hash and len(d.content_hash) == 64
        # File stored inside project analysis dir, not the source path
        assert str(tmp_path) in d.file_path
        assert "block_analysis" in d.file_path

    def test_uploadedfile_object_accepted(self, phase_c_doc, tmp_path):
        wav = _make_wav(tmp_path / "src2" / "u.wav", 3000)
        up = _FakeUpload("voice.wav", wav.read_bytes())
        doc = svc = AudioDeliveryService(projects_dir=tmp_path)
        doc = svc.upload_block_audio("proj_test", "blk_1", up)
        d = doc.get_block("blk_1").audio_delivery
        assert abs(d.actual_duration_ms - 3000) < 100

    def test_non_audio_file_rejected(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        fake = tmp_path / "notaudio.wav"
        fake.write_text("this is not audio at all")
        with pytest.raises(Exception, match="probe|audio"):
            svc.upload_block_audio("proj_test", "blk_1", str(fake))

    def test_bad_extension_rejected(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        exe = tmp_path / "evil.exe"
        exe.write_bytes(b"MZ...")
        with pytest.raises(Exception, match="not a supported audio"):
            svc.upload_block_audio("proj_test", "blk_1", str(exe))

    def test_block_isolation_no_cross_mapping(self, phase_c_doc, tmp_path):
        """Audio uploaded for blk_1 must not attach to blk_2."""
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src3" / "a.wav", 4000)
        doc = svc.upload_block_audio("proj_test", "blk_1", str(wav))
        assert doc.get_block("blk_1").audio_delivery is not None
        assert doc.get_block("blk_2").audio_delivery is None

    def test_short_audio_gap_warning(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src4" / "short.wav", 3000)  # window is 5000
        doc = svc.upload_block_audio("proj_test", "blk_1", str(wav))
        d = doc.get_block("blk_1").audio_delivery
        assert any("ngắn hơn" in w for w in d.warnings)
        report = svc.get_delivery_report("proj_test")
        assert any("ngắn hơn" in w for w in report["warnings"])

    def test_long_audio_overflow_warning_and_gate(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src5" / "long.wav", 7000)  # window is 5000
        doc = svc.upload_block_audio("proj_test", "blk_1", str(wav))
        d = doc.get_block("blk_1").audio_delivery
        # preserve_speed must NOT auto-trim or auto-speed: duration intact
        assert d.actual_duration_ms >= 7000
        assert any("dài hơn" in w for w in d.warnings)
        # Approve gate rejects unresolved fit warnings under preserve_speed
        with pytest.raises(Exception, match="fit warnings unresolved"):
            svc.approve_block_audio("proj_test", "blk_1")

    def test_fit_mode_selection_resolves_gate(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src6" / "long2.wav", 7000)
        svc.upload_block_audio("proj_test", "blk_1", str(wav))
        doc = svc.set_fit_mode("proj_test", "blk_1", AudioFitMode.TRIM_EDGE_SILENCE.value)
        d = doc.get_block("blk_1").audio_delivery
        assert d.fit_mode == AudioFitMode.TRIM_EDGE_SILENCE.value
        # Audio still not trimmed automatically (user must trigger processing)
        assert d.actual_duration_ms >= 7000
        approved = svc.approve_block_audio("proj_test", "blk_1")
        assert approved.get_block("blk_1").audio_delivery.approved is True

    def test_invalid_fit_mode_rejected(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src7" / "x.wav", 5000)
        svc.upload_block_audio("proj_test", "blk_1", str(wav))
        with pytest.raises(Exception, match="Invalid fit_mode"):
            svc.set_fit_mode("proj_test", "blk_1", "auto_cut_everything")

    def test_replacing_audio_invalidates_approval(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav1 = _make_wav(tmp_path / "src8" / "v1.wav", 5000)
        svc.upload_block_audio("proj_test", "blk_1", str(wav1))
        svc.approve_block_audio("proj_test", "blk_1")
        assert svc.get_document("proj_test").get_block("blk_1").audio_delivery.approved

        wav2 = _make_wav(tmp_path / "src9" / "v2.wav", 5000)
        doc = svc.upload_block_audio("proj_test", "blk_1", str(wav2))
        d = doc.get_block("blk_1").audio_delivery
        assert d.approved is False
        assert d.status == AudioDeliveryStatus.UPLOADED

    def test_set_fit_mode_invalidates_approval(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src10" / "v.wav", 5000)
        svc.upload_block_audio("proj_test", "blk_1", str(wav))
        svc.approve_block_audio("proj_test", "blk_1")
        doc = svc.set_fit_mode("proj_test", "blk_1", AudioFitMode.MANUAL_TRIM.value)
        assert doc.get_block("blk_1").audio_delivery.approved is False

    def test_trim_edge_silence_produces_shorter_file(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        # 1s silence + 2s tone + 1s silence
        wav = _make_wav(tmp_path / "src11" / "sil.wav", 4000)
        out = svc.trim_edge_silence(wav)
        assert out.exists()
        info = svc.probe_block_audio(out)
        assert int(info["duration_ms"]) <= 4000

    def test_overlap_rejected_on_approve(self, phase_c_doc, tmp_path):
        """Two overlapping blocks cannot both be audio-approved."""
        svc = AudioDeliveryService(projects_dir=tmp_path)
        # Shrink blk_2's window so it overlaps blk_1 (5000-8000 vs 0-5000 ok,
        # so instead extend blk_1's window via a direct document edit)
        doc_path = tmp_path / "proj_test" / "block_analysis" / "document.json"
        data = json.loads(doc_path.read_text(encoding="utf-8"))
        # Overlapping windows: blk_1 = 0-6000, blk_2 = 5000-10000
        # script_final stays empty → schema narration-overlap validator not triggered
        data["content_blocks"][0]["end_ms"] = 6000
        data["content_blocks"][0]["source_end_ms"] = 6000
        doc_path.write_text(json.dumps(data), encoding="utf-8")

        wav1 = _make_wav(tmp_path / "src12" / "o1.wav", 5000)
        wav2 = _make_wav(tmp_path / "src13" / "o2.wav", 5000)
        svc.upload_block_audio("proj_test", "blk_1", str(wav1))
        svc.upload_block_audio("proj_test", "blk_2", str(wav2))
        svc.approve_block_audio("proj_test", "blk_1")
        with pytest.raises(Exception, match="overlaps"):
            svc.approve_block_audio("proj_test", "blk_2")

    def test_missing_file_rejected_on_approve(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src14" / "gone.wav", 5000)
        svc.upload_block_audio("proj_test", "blk_1", str(wav))
        # Delete the stored copy
        stored = Path(svc.get_document("proj_test").get_block("blk_1").audio_delivery.file_path)
        stored.unlink()
        with pytest.raises(Exception, match="missing"):
            svc.approve_block_audio("proj_test", "blk_1")

    def test_delivery_report_shape(self, phase_c_doc, tmp_path):
        svc = AudioDeliveryService(projects_dir=tmp_path)
        wav = _make_wav(tmp_path / "src15" / "r.wav", 5000)
        svc.upload_block_audio("proj_test", "blk_1", str(wav))
        report = svc.get_delivery_report("proj_test")
        assert report["total_blocks"] == 2
        assert report["approved"] == 0
        assert report["pending"] == 2
        assert report["render_ready"] is False
        assert len(report["blocks"]) == 2
        b1 = report["blocks"][0]
        assert b1["expected_duration_ms"] == 5000
        assert b1["status"] == "uploaded"

        svc.approve_block_audio("proj_test", "blk_1")
        wav2 = _make_wav(tmp_path / "src16" / "r2.wav", 5000)
        svc.upload_block_audio("proj_test", "blk_2", str(wav2))
        svc.approve_block_audio("proj_test", "blk_2")
        report2 = svc.get_delivery_report("proj_test")
        assert report2["approved"] == 2
        assert report2["pending"] == 0
        assert report2["render_ready"] is True