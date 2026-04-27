/**
 * @file jwt.go
 * @description JWT 인증 미들웨어 — HMAC-SHA256 기반 토큰 검증
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-19 | 최초 생성 | JWT 인증 + 레거시 API Key 하위호환
 * ─────────────────────────────────────────
 */

package api

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

// JWTClaims represents the payload of a VBGW JWT token.
type JWTClaims struct {
	Sub string `json:"sub"`            // Subject (client ID)
	Iss string `json:"iss"`            // Issuer
	Exp int64  `json:"exp"`            // Expiration (Unix timestamp)
	Iat int64  `json:"iat"`            // Issued at
	Scope string `json:"scope,omitempty"` // Optional: "admin", "readonly", "calls"
}

// JWTAuthMiddleware validates JWT Bearer tokens with HMAC-SHA256.
// Falls back to static API Key validation for backward compatibility.
func JWTAuthMiddleware(jwtSecret, legacyAPIKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			authHeader := r.Header.Get("Authorization")
			if authHeader == "" {
				http.Error(w, `{"error":"authorization header required"}`, http.StatusUnauthorized)
				return
			}

			token := strings.TrimPrefix(authHeader, "Bearer ")
			if token == authHeader {
				http.Error(w, `{"error":"bearer token required"}`, http.StatusUnauthorized)
				return
			}

			// Path 1: Legacy static API Key (backward compatible)
			if token == legacyAPIKey {
				next.ServeHTTP(w, r)
				return
			}

			// Path 2: JWT validation (only if JWT_SECRET is configured)
			if jwtSecret == "" {
				http.Error(w, `{"error":"invalid token"}`, http.StatusUnauthorized)
				return
			}

			claims, err := ValidateJWT(token, jwtSecret)
			if err != nil {
				slog.Warn("JWT validation failed", "err", err, "remote", r.RemoteAddr)
				http.Error(w, fmt.Sprintf(`{"error":"%s"}`, err.Error()), http.StatusUnauthorized)
				return
			}

			slog.Debug("JWT authenticated", "sub", claims.Sub, "scope", claims.Scope)
			next.ServeHTTP(w, r)
		})
	}
}

// ValidateJWT parses and verifies a HMAC-SHA256 JWT token.
func ValidateJWT(tokenStr, secret string) (*JWTClaims, error) {
	parts := strings.Split(tokenStr, ".")
	if len(parts) != 3 {
		return nil, fmt.Errorf("malformed JWT: expected 3 parts, got %d", len(parts))
	}

	// Verify signature (HMAC-SHA256)
	signingInput := parts[0] + "." + parts[1]
	expectedSig, err := computeHMAC256(signingInput, secret)
	if err != nil {
		return nil, fmt.Errorf("signature computation failed: %w", err)
	}

	actualSig := parts[2]
	if !hmac.Equal([]byte(expectedSig), []byte(actualSig)) {
		return nil, fmt.Errorf("invalid signature")
	}

	// Verify header algorithm
	headerJSON, err := base64URLDecode(parts[0])
	if err != nil {
		return nil, fmt.Errorf("header decode failed: %w", err)
	}
	var header struct {
		Alg string `json:"alg"`
		Typ string `json:"typ"`
	}
	if err := json.Unmarshal(headerJSON, &header); err != nil {
		return nil, fmt.Errorf("header parse failed: %w", err)
	}
	if header.Alg != "HS256" {
		return nil, fmt.Errorf("unsupported algorithm: %s (only HS256)", header.Alg)
	}

	// Decode claims
	claimsJSON, err := base64URLDecode(parts[1])
	if err != nil {
		return nil, fmt.Errorf("claims decode failed: %w", err)
	}
	var claims JWTClaims
	if err := json.Unmarshal(claimsJSON, &claims); err != nil {
		return nil, fmt.Errorf("claims parse failed: %w", err)
	}

	// Validate expiration
	if claims.Exp > 0 && time.Now().Unix() > claims.Exp {
		return nil, fmt.Errorf("token expired")
	}

	return &claims, nil
}

// GenerateJWT creates a signed JWT token (utility for tests and admin scripts).
func GenerateJWT(secret, sub, issuer, scope string, ttl time.Duration) (string, error) {
	header := base64URLEncode([]byte(`{"alg":"HS256","typ":"JWT"}`))

	now := time.Now()
	claims := JWTClaims{
		Sub:   sub,
		Iss:   issuer,
		Iat:   now.Unix(),
		Exp:   now.Add(ttl).Unix(),
		Scope: scope,
	}
	claimsJSON, _ := json.Marshal(claims)
	payload := base64URLEncode(claimsJSON)

	signingInput := header + "." + payload
	signature, err := computeHMAC256(signingInput, secret)
	if err != nil {
		return "", err
	}

	return signingInput + "." + signature, nil
}

func computeHMAC256(data, secret string) (string, error) {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(data))
	return base64URLEncode(mac.Sum(nil)), nil
}

func base64URLEncode(data []byte) string {
	return base64.RawURLEncoding.EncodeToString(data)
}

func base64URLDecode(s string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(s)
}
