import "server-only";

export interface ServerEnv {
  apiBaseUrl: string;
  sessionSecret: string;
  dingtalkClientId: string;
  dingtalkRedirectUri: string;
  dingtalkAuthorizeUrl: string;
}

export function getServerEnv(): ServerEnv {
  const apiBaseUrl = process.env.API_BASE_URL;
  const sessionSecret = process.env.SESSION_COOKIE_SECRET;
  const dingtalkClientId = process.env.DINGTALK_CLIENT_ID;
  const dingtalkRedirectUri = process.env.DINGTALK_REDIRECT_URI;
  const dingtalkAuthorizeUrl =
    process.env.DINGTALK_AUTHORIZE_URL ?? "https://login.dingtalk.com/oauth2/auth";
  if (!apiBaseUrl || !sessionSecret || !dingtalkClientId || !dingtalkRedirectUri) {
    throw new Error("Missing required server env (API_BASE_URL, SESSION_COOKIE_SECRET, DINGTALK_CLIENT_ID, DINGTALK_REDIRECT_URI)");
  }
  if (sessionSecret.length < 32 || sessionSecret === "dev-only-change-me-32-bytes-minimum-xx") {
    throw new Error("SESSION_COOKIE_SECRET must be a real secret of at least 32 bytes (not the placeholder)");
  }
  return { apiBaseUrl, sessionSecret, dingtalkClientId, dingtalkRedirectUri, dingtalkAuthorizeUrl };
}
