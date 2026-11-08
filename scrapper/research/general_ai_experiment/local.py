"""Sequential, offline-only small MLX candidate. Never downloads a model."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import sys
import time

from .protocol import LOCAL, task_text, validate_output
from .runner import OUT, ROOT, SMOKE_PAGES, check_freeze, context_for, dump, load, sha
from .verification import verify


def run(checkpoint, limit):
    from scrapper.research.document_intelligence.offline import deny_network
    prepared = OUT / 'development'
    check_freeze(prepared)
    result_dir = OUT / 'runs/local-smoke'
    result_dir.mkdir(parents=True, exist_ok=True)
    status = {'requested_model': LOCAL, 'status': 'not_run', 'openrouter_cost_usd': 0}
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        status.update(status='incompatible_host')
    elif importlib.util.find_spec('mlx_vlm') is None:
        status.update(status='blocked', reason='mlx_vlm_not_installed_in_this_python')
    elif not checkpoint or not Path(checkpoint).is_dir():
        status.update(status='blocked', reason='requested_4B_checkpoint_not_cached_no_download_attempted')
    else:
        path = Path(checkpoint).resolve()
        review_path = OUT / 'local-checkpoint-review.json'
        review = load(review_path) if review_path.exists() else {}
        config = path / 'config.json'
        weights = sorted(path.glob('*.safetensors'))
        if not weights:
            status.update(status='blocked', reason='checkpoint_download_incomplete_no_weights', runtime_installed=True)
            dump(result_dir / 'status.json', status)
            return status
        if review.get('requested_id') != LOCAL or not review.get('compatibility_basis') or not review.get('reviewer'):
            raise ValueError('Local 4B checkpoint compatibility review required; 9B/27B are not substitutions')
        if not weights or sum(p.stat().st_size for p in weights) > 4 * 1024**3:
            raise ValueError('Missing or oversized weights; massive local models prohibited')
        identity = {p.name: sha(p) for p in weights + [config]}
        if identity != review.get('file_sha256'):
            raise ValueError('Local reviewed checkpoint hash mismatch')
        # Shared lock plus host process check. Do not terminate somebody else's inference.
        with (OUT / 'execution.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            processes = subprocess.run(['ps', '-axo', 'pid=,comm='], capture_output=True, text=True, check=True).stdout
            if any(term in processes.lower() for term in ('llama-server', 'mlx_vlm', 'mlx_lm', 'llm-engine')):
                raise ValueError('Competing local inference process detected')
            os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
            def timeout(*_):
                raise TimeoutError('Local inference time ceiling')
            signal.signal(signal.SIGALRM, timeout)
            signal.alarm(120)
            started = time.perf_counter()
            with deny_network():
                import mlx.core as mx
                from mlx_vlm import load as load_model, generate, apply_chat_template, __version__
                mx.set_memory_limit(8 * 1024**3)
                mx.set_cache_limit(512 * 1024**2)
                model, processor = load_model(str(path), lazy=False, strict=True)
                load_seconds = time.perf_counter() - started
                sent = 0
                rows = load(prepared / 'manifest.json')['documents']
                rows = [next(r for r in rows if r['document_type'] == kind) for kind in ('credit_rating', 'presentation', 'transcript')]
                for row in rows:
                    directory = prepared / row['content_sha256']
                    packet = next(p for p in load(directory / 'packets.json') if p['selected_page'] == SMOKE_PAGES[row['document_type']])
                    output = result_dir / (row['content_sha256'] + '.json')
                    if output.exists():
                        continue
                    if sent >= limit:
                        break
                    before = time.perf_counter()
                    prompt = (prepared / 'prompt.txt').read_text() + '\nFor this page-window request, propose events supported on selected_page only. Other original pages supply context, not additional target events.\n' + json.dumps({k: v for k, v in packet.items() if k not in {'image_file', 'image_sha256'}}, ensure_ascii=False)
                    formatted = apply_chat_template(processor, model.config, prompt, num_images=1, enable_thinking=False)
                    dump(result_dir / 'active-request.json', {'document_id': row['document_id'],
                        'page': packet['selected_page'], 'source_image_sha256': packet['image_sha256'],
                        'prompt_sha256': sha(prepared / 'prompt.txt'), 'max_output_tokens': 4096})
                    signal.alarm(120)
                    response = generate(model, processor, formatted,
                        image=[str(directory / packet['image_file'])], max_tokens=4096,
                        temperature=0, verbose=False, prefill_step_size=256)
                    raw = response.text
                    try:
                        parsed = json.loads(raw)
                        valid = validate_output(parsed)
                    except (ValueError, TypeError):
                        parsed, valid = None, False
                    dump(output, {'requested_model': LOCAL, 'model_identity': identity, 'runtime': __version__,
                        'strategy': 'pages', 'document_id': row['document_id'], 'page': packet['selected_page'],
                        'prompt_sha256': sha(prepared / 'prompt.txt'), 'source_image_sha256': packet['image_sha256'],
                        'max_output_tokens': 4096, 'source_text_encoding': 'literal_unicode_json',
                        'preprocessing': packet['preprocessing'], 'seconds': time.perf_counter() - before,
                        'response_text': raw, 'schema_valid': valid,
                        'input_tokens': int(response.prompt_tokens), 'output_tokens': int(response.generation_tokens),
                        'mlx_peak_memory_bytes': mx.get_peak_memory(),
                        'rss_highwater_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        'proposals': [verify(e, context_for(row), load(directory / 'canonical.json'), load(directory / 'source-units.json')) for e in parsed['events']] if valid else [],
                        'openrouter_cost_usd': 0, 'semantic_accuracy': None})
                    sent += 1
                    mx.clear_cache()
                    if mx.get_peak_memory() > 10 * 1024**3:
                        raise MemoryError('Local measured memory ceiling reached')
                signal.alarm(0)
                status.update(status='completed', documents=sent, load_seconds=load_seconds,
                    seconds=time.perf_counter() - started, peak_mlx_bytes=mx.get_peak_memory())
    dump(result_dir / 'status.json', status)
    return status


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', default=os.getenv('PHASE5_LOCAL_CHECKPOINT'))
    p.add_argument('--limit', type=int, default=3)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = p.parse_args()
    if not 1 <= args.limit <= 11:
        p.error('limit must be 1..11')
    if not args.worker:
        # Python signals cannot interrupt a long native MLX kernel promptly.
        # Bound the entire smoke worker externally, including model loading.
        command = [sys.executable, '-m', 'scrapper.research.general_ai_experiment.local',
                   '--worker', '--limit', str(args.limit)]
        if args.checkpoint:
            command += ['--checkpoint', args.checkpoint]
        try:
            completed = subprocess.run(command, timeout=125, check=False)
            raise SystemExit(completed.returncode)
        except subprocess.TimeoutExpired:
            status = {'requested_model': LOCAL, 'status': 'failed',
                'failure_type': 'HardWorkerDeadline', 'whole_worker_ceiling_seconds': 125,
                'memory_measurement': 'unavailable_after_forced_termination', 'no_automatic_retry': True}
            dump(OUT / 'runs/local-smoke/failure.json', status)
            print(json.dumps(status))
            raise SystemExit(1)
    try:
        print(json.dumps(run(args.checkpoint, args.limit), indent=2))
    except Exception as exc:
        signal.alarm(0)
        mx = sys.modules.get('mlx.core')
        status = {'requested_model': LOCAL, 'status': 'failed', 'failure_type': type(exc).__name__,
            'no_automatic_retry': True, 'rss_highwater_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'mlx_peak_memory_bytes': mx.get_peak_memory() if mx else None}
        dump(OUT / 'runs/local-smoke/failure.json', status)
        print(json.dumps(status))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
