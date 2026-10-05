import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { loadTrendDataset } from '../src/trends.js';

for (const delimiter of [',', '\t']) {
  for (const newline of ['\n', '\r\n']) {
    for (const commentedHeader of [false, true]) {
      test(`quoted fields delimiter=${JSON.stringify(delimiter)} newline=${JSON.stringify(newline)} header=${commentedHeader}`, async () => {
        const root = await mkdtemp(join(tmpdir(), 'mcp-quoted-'));
        const note = `first${newline}#literal${delimiter}text${newline}${newline}last "quoted"`;
        const cell = (value: string) => `"${value.replaceAll('"', '""')}"`;
        const header = ['query', 'date', 'notes'].join(delimiter);
        const text = '# metadata\n\n' + (commentedHeader ? '# ' : '') + header + newline
          + ['synthetic', '2026-10-01', note].map(cell).join(delimiter) + newline
          + ['control', '2026-10-02', 'ordinary note'].map(cell).join(delimiter) + newline
          + '# ignored "comment\n';
        try {
          await writeFile(join(root, delimiter === ',' ? 'fixture.csv' : 'fixture.tsv'), text);
          const result = await loadTrendDataset(root);
          assert.equal(result.rows.length, 2);
          assert.equal(result.rows[0].notable_event, note);
          assert.equal(result.rows[1].query, 'control');
          assert.equal(result.rows[1].notable_event, 'ordinary note');
        } finally { await rm(root, { recursive: true, force: true }); }
      });
    }
  }
}

test('ordinary records and comment-only files retain existing behavior', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mcp-quoted-controls-'));
  try {
    await writeFile(join(root, 'empty.csv'), '# metadata\n\n');
    await writeFile(join(root, 'ordinary.csv'), 'query,notes\nplain,normal\n');
    const result = await loadTrendDataset(root);
    assert.equal(result.rows.length, 1);
    assert.equal(result.rows[0].notable_event, 'normal');
  } finally { await rm(root, { recursive: true, force: true }); }
});
