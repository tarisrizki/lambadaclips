// Configuration for API endpoints
// If VITE_API_URL is set (e.g. in production), use it.
// Otherwise, default to empty string which means relative paths (proxied in dev).

export const API_BASE_URL = import.meta.env.VITE_API_URL || '';
export const API_ACCESS_KEY_STORAGE_KEY = 'api_access_key';

export const getApiUrl = (path) => {
    if (path.startsWith('http')) return path;
    // Ensure path starts with / if not present
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${API_BASE_URL}${normalizedPath}`;
};

export const installAuthenticatedFetch = () => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
        const rawUrl = typeof input === 'string' ? input : input.url;
        const requestUrl = new URL(rawUrl, window.location.origin);
        const apiOrigin = new URL(API_BASE_URL || window.location.origin, window.location.origin).origin;
        const isApiRequest = requestUrl.origin === apiOrigin && requestUrl.pathname.startsWith('/api/');

        if (!isApiRequest) return nativeFetch(input, init);

        const headers = new Headers(
            init.headers || (typeof input !== 'string' ? input.headers : undefined)
        );
        const accessKey =
            localStorage.getItem(API_ACCESS_KEY_STORAGE_KEY) ||
            import.meta.env.VITE_API_ACCESS_KEY ||
            '';
        if (accessKey) headers.set('X-API-Key', accessKey);
        return nativeFetch(input, { ...init, headers });
    };
};
