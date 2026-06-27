# Development Workflow

Version: 0.2
Status: Active

---

# Purpose

This document defines how Project Sentinel is developed, deployed and maintained.

Following these rules ensures consistency between development and runtime environments and minimizes the risk of introducing errors.

---

# Machine Roles

## Mac Mini

Development machine.

Responsibilities:

- Source code
- Documentation
- Architecture
- Testing
- Deployment

---

## Raspberry Pi

Runtime machine.

Responsibilities:

- Execute deployed code
- Runtime testing
- Hardware validation

The Raspberry Pi is not a development environment.

---

# Source of Truth

The Mac Mini is the single source of truth.

The Git repository is the authoritative record of the project.

The Raspberry Pi is a deployment target.

Running systems are not considered the source of truth.

All source code and documentation originate from the Mac Mini.

The Raspberry Pi receives deployments only.

---

# Project Locations

## Mac Mini

Project Root

```
~/Documents/Project Sentinel
```

Source Code

```
~/Documents/Project Sentinel/Source
```

Documentation

```
~/Documents/Project Sentinel/Docs
```

---

## Raspberry Pi

Runtime

```
/home/MiniBerry/drs
```

---

# Deployment

Deployments shall be performed only through:

```
deploy.sh
```

Manual copying of source files is not permitted.

Every deployment must be verified before development continues.

---

# Editing Rules

Production code shall never be edited directly on the Raspberry Pi.

If an emergency modification is made on the Raspberry Pi, it must immediately be copied back to the Mac Mini.

---

# Folder Responsibilities

Docs

Documentation

Source

Application source code

Tests

Testing code

Runtime

Deployment utilities and runtime assets

Assets

Icons, images, sounds and interface resources

Reports

Generated reports

Archive

Historical versions and backups

Case Management

Customer and case-related material

---

# Engineering Rule

Every new subsystem must define:

- one responsibility
- one architecture role
- one deployment method
- one verification step

---

# Session Checklist

Development begins only after both systems have been verified.

## Mac Mini

- Verify project structure.
- Review pending documentation updates.
- Confirm the source tree is complete.

## Raspberry Pi

- Verify the runtime structure.
- Confirm the latest deployment.
- Run `drs-status`.

---

# Working Principle

Slow is smooth.

Smooth is fast.

A clean architecture is always preferred over a quick implementation.
