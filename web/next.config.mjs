import { backendOrigin } from "./lib/backend-origin.mjs";

// Validate a configured destination at build time as well as in the route.
// Missing configuration is diagnosed by the API route with JSON/503, not a 404.
backendOrigin(process.env.JIRAYA_BACKEND_URL);

export default {};
