/**
 * CSRF double-submit token 헬퍼 (Phase N — N1.11).
 *
 * 패턴: __csrf__ 쿠키 값을 읽어 X-CSRF-Token 헤더에 붙임.
 * HttpOnly 쿠키(portal_access/portal_refresh)는 JS 가 읽을 수 없지만
 * __csrf__ 쿠키는 httponly=False → 이 헬퍼가 읽어서 header 로 붙임.
 */

export function getCsrfToken(): string {
  const match = document.cookie.split("; ").find((c) => c.startsWith("__csrf__="));
  return match ? decodeURIComponent(match.split("=")[1]) : "";
}

export function csrfHeaders(): Record<string, string> {
  const token = getCsrfToken();
  return token ? { "X-CSRF-Token": token } : {};
}
