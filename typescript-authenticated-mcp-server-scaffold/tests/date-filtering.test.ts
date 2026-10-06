import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { queryAirfareTrends } from '../src/trends.ts';

const invalidDates = [
  ['2025-02-29', '2025-03-01'],
  ['2026-02-30', '2026-03-02'],
  ['2026/04/31', '2026-05-01'],
  ['2026-13-01', '2027-01-01'],
  ['2026-00-01', '2025-12-01'],
  ['2026-01-00', '2025-12-31'],
  ['2025-W53', '2025-12-29'],
  ['2026-W00', '2025-12-22'],
  ['2026-W54', '2027-01-04'],
  ['2021-W53', '2022-01-03'],
] as const;

for (const [invalid, normalized] of invalidDates) {
  test(`invalid snapshot ${invalid} does not match ${normalized}`, async () => {
    const directory = await mkdtemp(join(tmpdir(), 'mcpkit-dates-'));
    try {
      const rows = [
        { query: 'Synthetic malformed date', snapshot_date: invalid },
        { query: 'Synthetic valid date', snapshot_date: normalized },
      ];
      await writeFile(join(directory, 'fixture.json'), JSON.stringify(rows));
      const filtered = await queryAirfareTrends(directory, { snapshotDate: normalized });
      assert.equal(filtered.total_rows, 2);
      assert.equal(filtered.matched_rows, 1);
      assert.equal(filtered.rows_returned, 1);
      assert.equal(filtered.rows[0]?.query, 'Synthetic valid date');
      const unfiltered = await queryAirfareTrends(directory, {});
      assert.equal(unfiltered.rows.length, 2);
      assert.equal(unfiltered.rows[0]?.snapshot_date, normalized);
      assert.equal(unfiltered.rows[1]?.snapshot_date, invalid);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
}

const validDates = [
  ['2024-02-29', '2024-02-29'],
  ['2000/02/29', '2000-02-29'],
  [' 2026-10-06 ', '2026-10-06'],
  ['2020-W53', '2020-12-28'],
  ['2026-W53', '2026-12-28'],
  ['2025-W01', '2024-12-30'],
  ['2026-w01', '2025-12-29'],
  ['0001-01-01', '0001-01-01T00:00:00.000Z'],
  ['0099/01/01', '0099-01-01T00:00:00.000Z'],
  ['0001-W01', '0001-01-01T00:00:00.000Z'],
  ['2026-10-06T02:00:00+02:00', '2026-10-06'],
] as const;

for (const [snapshot, filter] of validDates) {
  test(`valid snapshot ${snapshot} retains its calendar meaning`, async () => {
    const directory = await mkdtemp(join(tmpdir(), 'mcpkit-dates-'));
    try {
      await writeFile(join(directory, 'fixture.json'), JSON.stringify([
        { query: 'Synthetic matching date', snapshot_date: snapshot },
        { query: 'Synthetic other date', snapshot_date: '2026-06-15' },
      ]));
      const result = await queryAirfareTrends(directory, { snapshotDate: filter });
      assert.equal(result.matched_rows, 1);
      assert.equal(result.rows[0]?.query, 'Synthetic matching date');
      assert.equal(result.rows[0]?.snapshot_date, snapshot.trim());
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
}

test('valid filters, descending date order and limits remain unchanged', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'mcpkit-dates-'));
  try {
    await writeFile(join(directory, 'fixture.json'), JSON.stringify([
      { query: 'Synthetic older', snapshot_date: '2026-01-01', route: 'LHR-CDG' },
      { query: 'Synthetic newest', snapshot_date: '2026-10-06', route: 'LHR-CDG' },
      { query: 'Synthetic excluded', snapshot_date: '2026-10-07', route: 'LHR-JFK' },
      { query: 'Synthetic middle', snapshot_date: '2026-07-01', route: 'LHR-CDG' },
    ]));
    const result = await queryAirfareTrends(directory, { destinationAirport: 'CDG', limit: 2 });
    assert.equal(result.total_rows, 4);
    assert.equal(result.matched_rows, 3);
    assert.equal(result.rows_returned, 2);
    assert.deepEqual(result.rows.map(row => row.query), ['Synthetic newest', 'Synthetic middle']);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
