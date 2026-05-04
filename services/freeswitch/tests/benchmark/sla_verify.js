import http from 'k6/http';
import { check, sleep } from 'k6';

// SLA Criteria Configuration
export const options = {
    thresholds: {
        'http_req_duration{endpoint:create_call}': ['p(95)<150'], // Goal: 150ms
        'http_req_duration{endpoint:dtmf}': ['p(95)<50'],        // Goal: 50ms
        'http_req_failed': ['rate<0.001'],                      // Goal: 99.9% Success
    },
    scenarios: {
        ramp_up: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '30s', target: 50 }, // Ramp up to 50 concurrent users
                { duration: '1m', target: 50 },  // Stay at 50 users
                { duration: '30s', target: 0 },  // Ramp down
            ],
        },
    },
};

const BASE_URL = __ENV.API_URL || 'http://localhost:8080';
const API_KEY = __ENV.ADMIN_API_KEY || 'changeme-admin-key';

export default function () {
    const headers = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${API_KEY}`, // Supports legacy API key as bearer
    };

    // 1. Create Call
    let createRes = http.post(
        `${BASE_URL}/api/v1/calls`,
        JSON.stringify({ target_uri: 'sip:k6-test@proxy' }),
        { headers: headers, tags: { endpoint: 'create_call' } }
    );
    check(createRes, {
        'create call is 201': (r) => r.status === 201,
    });

    if (createRes.status === 201) {
        const callId = createRes.json().call_id;

        // 2. Send DTMF (Simulating IVR interaction)
        sleep(1);
        let dtmfRes = http.post(
            `${BASE_URL}/api/v1/calls/${callId}/dtmf`,
            JSON.stringify({ digits: '1' }),
            { headers: headers, tags: { endpoint: 'dtmf' } }
        );
        check(dtmfRes, {
            'dtmf is 200': (r) => r.status === 200,
        });

        // 3. Health Check
        let healthRes = http.get(`${BASE_URL}/health`, { headers: headers });
        check(healthRes, {
            'health is 200': (r) => r.status === 200,
        });
    }

    sleep(0.5);
}
