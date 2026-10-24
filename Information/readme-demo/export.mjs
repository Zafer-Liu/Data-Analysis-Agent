import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Flatten alpha and reduce the palette for a compact, self-contained README GIF.
const cwd = fileURLToPath(new URL('.', import.meta.url));
const filter = [
  'color=c=0x111827:s=1200x720:r=12[bg]',
  '[bg][0:v]overlay=shortest=1:format=auto,fps=10,scale=1000:-1:flags=lanczos,split[a][b]',
  '[a]palettegen=max_colors=96:reserve_transparent=0[p]',
  '[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle',
].join(';');
const result = spawnSync('ffmpeg', [
  '-hide_banner', '-loglevel', 'error', '-y', '-i', '.hyperframes/demo.gif',
  '-filter_complex', filter, '-loop', '0', '../assets/analysis-demo.gif',
], { cwd, stdio: 'inherit', windowsHide: true });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
