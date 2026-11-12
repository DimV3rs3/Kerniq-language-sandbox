# Language sandbox

The shell already has a private Python in `.kerniq-venv`. This plugin does the same for Node.js and .NET.

`node`, `npm`, `npx`, `dotnet`, and `csc` resolve to shims in `.kerniq-runtimes/bin`. A shim runs the private copy under `.kerniq-runtimes`, or stops. It does not search the computer. An absolute path to a host `node.exe` or `dotnet.exe` is refused, including when host freedom is on.

Prepare Node.js or .NET from the plugin's settings tab. Until then, those commands fail closed instead of using whatever is installed on the machine.
