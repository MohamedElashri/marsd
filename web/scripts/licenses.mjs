import fs from 'node:fs';
import path from 'node:path';
const packages = ['react', 'react-dom', 'scheduler'];
const text = packages
  .map(
    (name) =>
      `${name}\n${'='.repeat(name.length)}\n${fs.readFileSync(path.join('node_modules', name, 'LICENSE'), 'utf8')}`,
  )
  .join('\n\n');
const tauri = fs.readFileSync('node_modules/@tauri-apps/api/LICENSE-MIT', 'utf8');
fs.writeFileSync(
  '../src/marsd/static/third-party-licenses.txt',
  `${text}\n\n@tauri-apps/api (MIT option)\n${tauri}`,
);
