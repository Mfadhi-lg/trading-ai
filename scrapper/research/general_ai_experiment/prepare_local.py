"""Download only the explicitly requested small checkpoint, pinning its revision."""
import json
from .runner import OUT, dump, sha, now
from .protocol import LOCAL


def main():
    from huggingface_hub import HfApi, snapshot_download
    info = HfApi().model_info(LOCAL, files_metadata=True)
    allowed = [f.rfilename for f in info.siblings
               if f.rfilename.endswith(('.json', '.safetensors', '.jinja', '.txt', '.md'))]
    total = sum(f.size or 0 for f in info.siblings if f.rfilename in allowed)
    if not total or total > 4 * 1024**3:
        raise ValueError('Checkpoint exceeds small-model download bound')
    directory = OUT / 'checkpoint'
    dump(OUT / 'local-download-plan.json', {'model': LOCAL, 'revision': info.sha,
        'bytes': total, 'files': allowed, 'at': now()})
    # A single download worker; no inference process during acquisition.
    snapshot_download(LOCAL, revision=info.sha, local_dir=directory,
                      allow_patterns=allowed, max_workers=1)
    config = json.loads((directory / 'config.json').read_text())
    if config.get('model_type') != 'qwen3_5' or config.get('quantization', {}).get('bits') != 4 or config.get('text_config', {}).get('hidden_size') != 2560:
        raise ValueError('Downloaded checkpoint architecture differs from verified 4B model card')
    weights = sorted(directory.glob('*.safetensors'))
    dump(OUT / 'local-checkpoint-review.json', {'requested_id': LOCAL,
        'revision': info.sha, 'reviewer': 'Codex primary-source model-card and config inspection',
        'compatibility_basis': 'Exact requested repository, qwen3_5 multimodal architecture, 4-bit weights, 2560 hidden width; runtime compatibility still requires smoke test.',
        'file_sha256': {p.name: sha(p) for p in weights + [directory / 'config.json']},
        'total_download_bytes': total, 'path': str(directory), 'at': now()})
    print(json.dumps({'checkpoint': str(directory), 'bytes': total, 'revision': info.sha}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # HF delivery errors can contain signed URLs. Preserve only error class.
        dump(OUT / 'local-download-failure.json', {'at': now(), 'model': LOCAL,
             'status': 'download_failed', 'failure_type': type(exc).__name__,
             'inference_ran': False, 'no_model_substitution': True})
        print(json.dumps({'status': 'download_failed', 'failure_type': type(exc).__name__}))
        raise SystemExit(1)
