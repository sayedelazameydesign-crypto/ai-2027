# Project TODO

- [x] Add a persistent tasks data model for user-owned tasks.
- [x] Add tRPC procedures for task list, create, update text, toggle completion, and delete.
- [x] Apply the database schema migration and verify task persistence.
- [x] Build an elegant responsive dashboard layout for task management.
- [x] Add task creation with text input and add action.
- [x] Add inline task editing without a separate page.
- [x] Add individual task deletion with clear visual confirmation before execution.
- [x] Add completion checkbox with strikethrough styling for completed tasks.
- [x] Add filters for all, active, and completed tasks.
- [x] Add live dashboard counters for total, completed, and remaining tasks.
- [x] Add loading, empty, error, and mutation feedback states.
- [x] Write or update Vitest tests for task CRUD and validation.
- [ ] Run type checks, tests, and visual verification.
- [x] Prepare the finished project for synchronization with GitHub repository system-.
- [x] Run TypeScript checks and Vitest tests for auth, validation, and task CRUD routing.
- [ ] Perform authenticated visual verification of dashboard interactions and counters.

## Interactive E2E Verification

- [ ] Authenticate in the browser and confirm the Arabic dashboard loads after login.
- [ ] Verify create-task success, counters, long-title validation, and empty-title blocking.
- [ ] Verify inline editing through double-click and edit button, including invalid save validation.
- [ ] Verify completion toggle, strikethrough styling, and counter updates.
- [ ] Verify delete confirmation cancel/confirm behavior, toast, and counter updates.
- [ ] Verify all/active/completed filters and their stability after mutations.
- [ ] Verify empty state, reload persistence, RTL layout, and toast direction.
- [ ] Verify multi-user task isolation with a second authenticated user.
- [ ] Capture evidence screenshots or recording for steps 1–8 and report any defects.
- [ ] Re-verify fixes and commit with a fix(dashboard): prefix if defects are found.

## Authentication Compatibility Audit

- [x] Audit session source, cookie name, Authorization handling, official session creation, environment requirements, and test/development isolation without running seed or touching the database.

- [x] Create a quick GitHub connection and update-push guide.

- [x] Fix the duplicate clone command in GITHUB_GUIDE.md.
- [x] Verify the documented GitHub commands against the current repository state.
