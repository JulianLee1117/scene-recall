/**
 * Marks requests made from the app's own screens. The API records only these
 * in the local taste log (searches, plays and saves you make here), so scripts
 * and agents calling the same endpoints never shape it.
 */
export const APP_CLIENT_HEADERS = { "X-Scene-Recall-Client": "app" } as const;
