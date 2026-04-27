import http from 'k6/http';
import { check, sleep } from 'k6';

// Test config: Rapid spike mimicking Thundering Herd
export let options = {
    stages: [
        { duration: '5s', target: 50 },  // Ramp up to 50 concurrent fs_cli threads
        { duration: '15s', target: 200 }, // Spike to 200 concurrent XML queries
        { duration: '5s', target: 0 },   // Cool down
    ],
    thresholds: {
        // We mandate 95% of Dialplan requests fulfill under 15ms.
        http_req_duration: ['p(95)<15'],
        // Zero failures accepted for dialplan.
        http_req_failed: ['rate==0'],
    },
};

export default function () {
    // Determine target from ENV var, defaulting to local docker compose Orchestrator
    const url = __ENV.TARGET_URL || 'http://127.0.0.1:8080/api/v1/fs/dialplan';

    // Simulate FreeSWITCH mod_xml_curl form values
    const payload = {
        'Caller-Caller-ID-Number': '9999',
        'Caller-Destination-Number': '1004',
        'Hunt-Context': 'default'
    };

    const params = {
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        },
    };

    let res = http.post(url, payload, params);

    check(res, {
        'status is 200': (r) => r.status === 200,
        'contains valid xml': (r) => r.body.includes('voicebot-inbound'),
    });

    sleep(0.01); // Minimal throttle inside VU looping
}
