package api

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAuthMiddleware_ValidKey(t *testing.T) {
	handler := AuthMiddleware("secret-key-12345")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	req.Header.Set("X-Admin-Key", "secret-key-12345")
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestAuthMiddleware_InvalidKey(t *testing.T) {
	handler := AuthMiddleware("secret-key-12345")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	req.Header.Set("X-Admin-Key", "wrong-key")
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", w.Code)
	}
}

func TestAuthMiddleware_MissingKey(t *testing.T) {
	handler := AuthMiddleware("secret-key-12345")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for missing key, got %d", w.Code)
	}
}

func TestRateLimitMiddleware_AllowsWithinBurst(t *testing.T) {
	handler := RateLimitMiddleware(100, 10)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	for i := 0; i < 5; i++ {
		req := httptest.NewRequest("GET", "/test", nil)
		req.RemoteAddr = "127.0.0.1:12345"
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("request %d: expected 200, got %d", i, w.Code)
		}
	}
}

func TestRateLimitMiddleware_RejectsOverBurst(t *testing.T) {
	// Very low rate to trigger rejection
	handler := RateLimitMiddleware(0.001, 1)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// First request should pass (uses burst)
	req := httptest.NewRequest("GET", "/test", nil)
	req.RemoteAddr = "127.0.0.1:12345"
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("first request should pass, got %d", w.Code)
	}

	// Subsequent requests should be rate limited
	rejected := false
	for i := 0; i < 10; i++ {
		req = httptest.NewRequest("GET", "/test", nil)
		req.RemoteAddr = "127.0.0.1:12345"
		w = httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		if w.Code == http.StatusTooManyRequests {
			rejected = true
			break
		}
	}
	if !rejected {
		t.Fatal("expected at least one 429 response")
	}
}

func TestLoopbackOnlyMiddleware_AllowsLocalhost(t *testing.T) {
	handler := LoopbackOnlyMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	for _, ip := range []string{"127.0.0.1:8080", "::1:8080"} {
		req := httptest.NewRequest("GET", "/test", nil)
		req.RemoteAddr = ip
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("expected 200 for %s, got %d", ip, w.Code)
		}
	}
}

func TestLoopbackOnlyMiddleware_RejectsExternal(t *testing.T) {
	handler := LoopbackOnlyMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	req.RemoteAddr = "192.168.1.100:8080"
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for external IP, got %d", w.Code)
	}
}

func TestMetricsMiddleware_RecordsStatusCode(t *testing.T) {
	handler := MetricsMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
	}))

	req := httptest.NewRequest("POST", "/api/v1/calls", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d", w.Code)
	}
}
