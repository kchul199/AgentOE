import http from 'k6/http';
import { check } from 'k6';

// Test config: Instant massive spikes simulating marketing-push bulk outbound
export let options = {
    scenarios: {
        capacity_test: {
            executor: 'shared-iterations',
            vus: 50,
            iterations: 500, // Send 500 calls instantly using 50 workers
            maxDuration: '10s',
        },
    },
};

export default function () {
    // Wait for auth if token is enabled, but for demo we assume backend auth is disabled or bypassed via test-env.
    // Replace API key if AuthMiddleware enforces it globally.
    const url = __ENV.TARGET_URL || 'http://127.0.0.1:8080/api/v1/calls';
    const apikey = __ENV.API_KEY || 'hardcore-admin-key';
    
    // Send standard Outbound JSON payload
    const payload = JSON.stringify({
        target_uri: `sip:100${__VU}@proxy.internal`,
        caller_id: '800-TEST-VBGW'
    });

    const params = {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apikey}`
        },
    };

    let res = http.post(url, payload, params);

    // With a MAX_SESSIONS limitation of e.g. 100, exactly 100 requests should get 201 Created.
    // The rest MUST get 503 Service Unavailable instantly.
    check(res, {
        'Call Created or Rejected properly': (r) => r.status === 201 || r.status === 503,
    });
}
