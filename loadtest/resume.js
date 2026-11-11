// k6 scenario: POST fixture resumes to /resume.
//
// Each iteration posts one PDF picked at random from the fixture pool, so
// request content repeats and the cache (when enabled) can hit. With
// FIXTURES=3 every fixture is cached within the first requests of a run,
// which makes the cache-on run an (almost) all-hit measurement — the
// contrast with the all-miss cache-off run is the point.
//
//   k6 run loadtest/resume.js
//
// Env knobs: BASE_URL (default http://127.0.0.1:8000), VUS (20),
// DURATION (30s), FIXTURES (3, must match generate_fixtures.py output).

import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const FIXTURES = Number(__ENV.FIXTURES || 3);

// open() only works in the init context; paths are relative to this file.
const files = [];
for (let i = 1; i <= FIXTURES; i++) {
  files.push(open(`./fixtures/resume_${i}.pdf`, 'b'));
}

export const options = {
  scenarios: {
    resume: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 20),
      duration: __ENV.DURATION || '30s',
      gracefulStop: '30s',
    },
  },
  // The cache-off run serializes behind the blocking upstream call, so
  // individual requests can queue for a while; don't let k6 cut them off.
  setupTimeout: '30s',
};

export default function () {
  const i = Math.floor(Math.random() * files.length);
  const res = http.post(
    `${BASE_URL}/resume`,
    { file: http.file(files[i], `resume_${i + 1}.pdf`, 'application/pdf') },
    { timeout: '60s' },
  );
  check(res, {
    'status is 200': (r) => r.status === 200,
    'has score': (r) => r.status === 200 && r.json('score') !== undefined,
  });
}
