Private instance template for Klokast deployments. 

klokast-instance is the template repository for a Klokast deployment instance. It contains the private, deployment-specific layer of the Klokast architecture: infrastructure topology, private identities and group membership, deployment-approved access capabilities, application resource bindings, application configuration, and local extensions.

The instance repository is designed to be used together with the public klokast-box repository, which provides the generic platform implementation, automation, and runtime components. Users should create their own private repository from this template and keep deployment-specific data separate from the upstream platform code.

Don't clone this repository directly. Create your own private repository from this template to define topology, identities, resource bindings, application configuration, and custom extensions on top of the Klokast platform.

This repository defines one Klokast instance.

It contains:
- instance topology and control-plane placement
- private identities and group membership
- deployment-approved access capabilities
- application resource bindings and configuration
- instance-specific extensions

It does NOT contain:
- Klokast implementation code
- secrets
- runtime state
- generated files

The implementation comes from:
https://github.com/klokast/klokast-box

# Boundaries

- `deployment.yml`:
  - What physical boxes and sites constitute this instance?
  - Where are mandatory control-plane authorities placed?

- `platform-resources.yml`:
  - Which applications and optional resources are enabled?
  - Where are they placed?
  
- upstream architecture:
  - What does every standard box contain automatically?

# Instance rules

The deployment schema `deployment.yml` shall follow these rules:

## Instance rules:
`instance.name`:
- required
- lowercase DNS-style identifier preferred

## Site rules: 
- at least one site must exist
- every box.site must reference an existing site
- timezone must be a valid IANA timezone
- country should be ISO 3166-1 alpha-2

## Box rules: 
- at least one box must exist
- box names must be unique
- box names must be valid lowercase DNS labels
- box names must not end in reserved role suffixes such as `-dom0`, `-router`, `-bak`, `-dmz`, `-iot`, `-usr`, and `-ops`.

## Controller rules
- `controller.active_box` is required
- `controller.active_box` must reference an existing box

- `controller.standby_box` is optional
- `controller.standby_box` must reference an existing box
- `controller.standby_box` must differ from active_box

## Airunner rules
- airunners must contain at least one entry
- every airrunner.box must reference an existing box
- the same box should not appear twice
