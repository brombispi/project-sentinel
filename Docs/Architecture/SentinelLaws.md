Project Sentinel

Sentinel Laws

Version: 0.1
Status: Active
Applies to: All Project Sentinel modules

⸻

Purpose

The Sentinel Laws define the engineering principles that govern every decision made by Project Sentinel.

They are intentionally independent of any programming language, operating system or hardware platform.

Every subsystem, including ARGUS, AEGIS, ORACLE, ARCHIVE, HERMES and future modules, must comply with these laws.

If software behaviour conflicts with a Sentinel Law, the software must be changed.

The laws are considered the highest level of the Project Sentinel architecture.

⸻

SL-001

Protect the Recovery Engine

The Recovery Engine shall never permit destructive operations on its own system drive.

The Recovery Engine must always remain operational and protected from accidental modification.

⸻

SL-002

Protect Original Media

The customer’s original storage device shall never be modified when a safer alternative exists.

Whenever reasonably possible, recovery operations shall be performed from a forensic image rather than directly from the original media.

⸻

SL-003

Observe Before Acting

Every storage device shall be identified and assessed before any recommendation or operation is made.

Unknown devices shall never be acted upon.

⸻

SL-004

Explain Every Decision

Every recommendation, approval, denial or warning shall include a clear explanation that the operator can understand.

Project Sentinel shall never produce unexplained decisions.

⸻

SL-005

Quantify Risk

Every recommendation shall include an estimated risk level and, whenever possible, a confidence level.

The operator should understand both the expected benefit and the potential consequences of every action.

⸻

SL-006

The Operator Decides

Project Sentinel provides guidance and recommendations.

Final responsibility for every operation remains with the operator.

Whenever uncertainty exceeds confidence, Project Sentinel shall recommend rather than decide.

⸻

Engineering Philosophy

Project Sentinel is designed around one fundamental objective:

Recover data while introducing the least possible risk to the customer’s original media.

Every feature, workflow and future module should support this objective.

If a proposed feature cannot improve safety, reliability, transparency or operator confidence, it should be reconsidered before implementation.

⸻

SL-007

Preserve Workflow

Every workflow shall follow a logical sequence that supports safe recovery practices.

Project Sentinel shall never encourage or permit actions that contradict a previous safety assessment.

Every subsequent recommendation shall remain consistent with all previous observations and decisions.