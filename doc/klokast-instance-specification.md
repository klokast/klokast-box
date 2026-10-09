# Instance desired state

`klokast-instance.json` is the desired state of one deployment. Keep it in a
private, standalone Git repository. It contains boxes, controller placement,
Tailnet members, connectivity capabilities, application placement and features,
and retained data. It must not contain secrets, runtime
observations, generated configuration, or executable code.

The installed CLI selects validation from `schema-version`. The `$schema` URL
is information for editors. It does not select privileged code. There is no
engine lock. The deployment property described in
[Platform lifecycle](platform-lifecycle.md) determines which implementation may
run.

The public [Instance schema](../schemas/klokast-instance-v1.schema.json) defines
the accepted fields. Public application manifests define available features
and placement modes. A deployment can have one box and no standby controller.
Applications can be present. Application-owned deployment code consumes the
resources and app-scoped inputs supplied by the Platform. A missing supported
implementation is an error; it does not permit arbitrary privileged code.
The foundation does not need an application-specific adapter.

Use `klokast init --instance PATH --values FILE` to create an Instance. Use
`klokast check --instance PATH` before committing a change. `klokast plan`
shows a preview. An optional Observation supplies facts for comparison only.
Neither command writes runtime state or creates execution authority.

The controller checkout is `~/private/klokast/instance`. Use ordinary Git to
edit, commit, push, and synchronize it. Observations cannot replace the
Instance as desired state. Retained data declarations remain
binding when an application is absent. Omission does not permit data deletion.

Active application entries accept only the fields in the Instance schema.
They cannot specify custom device bindings, app VM addresses, or builder
expiry fields in `apps`. Application workflows must stop when the validated
resource view lacks a required input. Do not add unsupported fields or use an
inactive application declaration to enable an application.

## Box country and download sources

`boxes.<box>.country` supplies the country for the Platform's
[public download source selection](architecture.md#public-download-sources).
Changing a box's country changes the sources selected at its next tool
convergence. No separate mirror field or box registry is required.

## AI runner placement

`airunners` is a nonempty ordered list of unique runtime names. It is the only
runner placement registry. `<box>-air` declares an optional Platform Xen guest
on a known box, including a box without a controller. Supported `<cloud>-ops`
runner names remain valid. During migration, `<box>-ops-airunner` is also valid
on the active or standby controller. The legacy and VM names can coexist.
Priority does not make lower-priority declared runners optional for checks.

The Platform owns guest allocation, the reserved `usr` address, enrollment,
network policy, image construction and lifecycle; see
[the runner architecture](architecture.md#box-air). Applications cannot request
this address or acquire the runner identity. Removing a declaration does not
delete a disk. Explicit retirement must verify test-only or retained data before
removing resources. A migration must preserve application and retained-data
declarations and must not change controller placement.

## Application names

The application names `platform` and `doctor` are reserved for the
[`kk` commands](../klokast-dev/README.md). They are forbidden in application
manifests and as keys in `apps` or `inactive-apps`, including applications
declared absent. Validation reports
an error; it does not rename the application. This restriction applies only
to application names.

## Application dependencies

An application manifest must declare the applications that it requires. The
Instance must explicitly declare each required application as present and
authorize the connections between them. Declaring an application does not
grant access to another application.

Validation must reject a missing or absent required application, an unsupported
dependency, and a dependency cycle before installation. It must not add or
install dependencies implicitly. For example, if an application requires
Nextcloud, the operator declares both applications and their required
connections in the Instance.

These requirements apply in development and production. Dependency fields and
dependency validation are not implemented in the current manifest and Instance
schemas. Do not add undeclared fields to an Instance file. Use the supported
application instructions for installation. See
[application installation](platform-lifecycle.md#application-installation).
