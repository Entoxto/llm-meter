// Host adapter: this companion always targets an explicit private server.
export const SidecarCredentials = {
  get: () => null,
  authorization: () => undefined,
}
