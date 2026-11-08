"""Build/export local candidate only; no production containers or mounts."""
import json
import subprocess
from prepare import ROOT, OUT, CONTEXT, ARCHIVE, COMMIT, RELEASE_ID, write

source = json.loads((OUT/'source-manifest.json').read_text())
cmd = ['docker', 'buildx', 'build', '--platform', 'linux/arm64', '--provenance', 'mode=max',
       '--sbom=true', '--build-arg', f"SOURCE_DATE_EPOCH={source['source_date_epoch']}",
       '--label', f'org.opencontainers.image.revision={COMMIT}',
       '--label', f'org.opencontainers.image.version={RELEASE_ID}',
       '--label', f"ai.trading.source.projection.sha256={source['source_projection_sha256']}",
       '--label', f"ai.trading.source.patch.sha256={source['patch_sha256']}",
       '--tag', 'trading-ai-ar02-combined-release:20261008',
       '--metadata-file', str(OUT/'build-metadata.json'),
       '--output', f'type=oci,dest={ARCHIVE},rewrite-timestamp=true', str(CONTEXT)]
write('build-command.json', cmd)
with (OUT/'runtime-before.log').open('w') as log:
    subprocess.run(['docker', 'ps', '-a', '--filter', 'label=com.docker.compose.project=trading-ai',
                    '--format', '{{.Names}} {{.State}} {{.Image}}'], stdout=log, check=True)
with (OUT/'build.log').open('w') as log:
    result = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
write('build-exit.json', {'exit_code': result.returncode})
raise SystemExit(result.returncode)
