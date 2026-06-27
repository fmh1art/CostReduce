# Evolve task

You are evolving bash helper scripts that downstream coding agents will call to solve similar tasks with fewer steps. The scripts you write will be bind-mounted into downstream agent containers at `/app/.preinstalled_scripts/<name>/main.sh`.

## Working directory
Your cwd is the absolute path shown below. Create/modify/delete files ONLY inside this directory. Each script lives under `./<name>/` with two files: `main.sh` (entrypoint) and `intro.json` (metadata).

## intro.json schema
Valid JSON with EXACTLY these fields (no extras, no `when_to_use`):
  {
    "name": "<script_name>",
    "description": "ONE sentence: what this script does.",
    "entrypoint": "main.sh",
    "parameters": [
      {"name": "...", "type": "string|int|bool", "required": true,
       "description": "ONE short phrase"}
    ],
    "examples": [{"call": "/app/.preinstalled_scripts/<name>/main.sh <args>",
                   "expected": "ONE short line"}],
    "cost_saving_rationale": "ONE short sentence: why this script saves cost"
  }
Rules:
- `description` ≤ 1 sentence. `parameter.description` ≤ 1 phrase. `cost_saving_rationale` ≤ 1 sentence. `examples[*].expected` ≤ 1 line.
- `examples` is OPTIONAL; at most ONE example. No verbose multi-step walkthroughs.
- `examples[*].call` MUST use the absolute path `/app/.preinstalled_scripts/<name>/main.sh ...` (this is the path downstream agents see after bind-mounting, not your cwd).
- Do NOT include `when_to_use` — the `description` already tells the agent when.
- Merge similar scripts: before creating a new directory, check whether an existing script could be extended with a new action/flag instead. Fewer, more general scripts = lower downstream prompt cost. When you remove a script, delete its directory.

## instruction.md (BEHAVIOR CONTRACT, NOT TOOL CATALOG)
Maintain instruction.md as ≤ 10 behavior contracts for the downstream agent. Each contract ≤ 1 line. Write contracts based on the cost patterns you observe in the samples below — do NOT just copy generic advice.
Good contracts name a specific stuck pattern and the action to take, e.g.:
  - "After N steps without attempting a fix, STOP exploring and attempt a fix."
  - "If you've made N edits without running tests, run tests now."
Do NOT write per-tool bullets like "Use read-lines to ..." — that's intro.json's job.
Do NOT write a hard step cap like "max 60 steps" — hard tasks legitimately need 100+ steps.
Instead, write STOP-PROGRESSING-IF rules that detect stuck behavior.

## Cost model (for prioritizing your designs)
Effect on real cost, largest to smallest:
  1. Fewer agent steps — each step costs cache write + output tokens.
  2. Shorter tool_call commands.
  3. Smaller observations — low priority.
A batching script that collapses N repeated calls into 1 step saves N-1 steps.
Do NOT optimize observation size if it costs you an extra step.

## Verification (REQUIRED after every script add/update)
1. Run `bash <script_dir>/main.sh <sample_args>` and confirm it exits 0.
2. Validate intro.json: `python -c "import json; json.load(open('<script_dir>/intro.json'))"`.
3. Re-read the script — confirm it is GENERIC (no hardcoded file paths from the samples below).

Working directory absolute path: `/tmp/v2_test/scripts`
The cwd_name shown in samples below is the basename of this path.


# Current scripts in this directory

## search-and-read/
intro.json:
{
  "name": "search-and-read",
  "description": "Search a regex pattern in a directory and print matching files + line numbers.",
  "entrypoint": "main.sh",
  "parameters": [
    {"name": "pattern", "type": "string", "required": true, "description": "regex to search"},
    {"name": "dir", "type": "string", "required": false, "description": "directory to search in"}
  ],
  "cost_saving_rationale": "Replaces repeated grep -rn with one call."
}

# Chunk 1

## Trajectory context
Task: Hosts need cancellation across nested evaluations, module phases, and queued jobs without discarding `Context`. Implement evaluation cancellation with parent/child handles and cancellation checkpoints. ## Required public capabilities - Public entry points must include: `Context::{new_evaluation_handle, new_child_evaluation_handle, eval_with_evaluation, enqueue_job_with_evaluation, run_jobs_with_ev...

## Graph Contrastive
Phase: read (action steps 1-14 of this trajectory, 14 steps total)
Anchor: step 14 (last successful write in this phase)
Minimal subgraph: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 14] (anchor + dependency closure, failed non-explore steps filtered out)
Removable steps (not in closure of anchor 14): [10, 11, 12, 13]. A batching script that skips or merges these would not affect the anchor's outcome.
### Original Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: find . -type f -name "*.rs" | head -50
- tool: bash
  command: ls -la
Observation:
returncode: 0
output:
./target/debug/build/serde_core-15f86ebe79c73b6f/out/private.rs
./target/debug/build/serde-ad0ce9851116ebb1/out/private.rs
./target/debug/build/thiserror-61914d7c68dd9648/out/private.rs
./cli/src/helper.rs
./cli/src/main.rs
./cli/src/executor.rs
./cli/src/debug/limits.rs
./cli/src/debug/function.rs
./cli/src/debug/string.rs
./cli/src/debug/mod.rs
./cli/src/debug/gc.rs
./cli/src/debug/realm.rs
./cli/src/debug/shape.rs
./cli/src/debug/optimizer.rs
./cli/src/debug/object.rs
./...<truncated>
returncode: 0
output:
total 508
drwxrwxrwx  1 root root   4096 Jun 11 06:29 .
drwxr-xr-x  1 root root   4096 Jun 21 02:15 ..
drwxr-xr-x  2 root root   4096 Jun 11 06:28 .cargo
drwxr-xr-x  2 root root   4096 Jun 11 06:28 .config
-rw-r--r--  1 root root    317 Jun 11 06:28 .editorconfig
drwxr-xr-x  1 root root   4096 Jun 11 19:40 .git
-rw-r--r--  1 root root    781 Jun 11 06:28 .gitattributes
drwxr-xr-x  4 root root   4096 Jun 11 06:28 .github
-rw-r--r--  1 root root    522 Jun 11 0...<truncated>

#### Step 2
Action:
- tool: bash
  command: find . -type f -name "*.rs" -not -path "./target/*" | sort
Observation:
returncode: 0

#### Step 3
Action:
- tool: bash
  command: find ./core -name "*.rs" -not -path "./target/*" | xargs grep -l "Context" 2>/dev/null | head -20
- tool: bash
  command: find ./core/engine/src -name "*.rs" | sort
Observation:
returncode: 0
output:
./core/ast/src/scope_analyzer.rs
./core/ast/src/scope.rs
./core/engine/src/symbol.rs
./core/engine/src/context/mod.rs
./core/engine/src/context/hooks.rs
./core/engine/src/try_into_js_result_impls.rs
./core/engine/src/interop/mod.rs
./core/engine/src/interop/into_js_arguments.rs
./core/engine/src/interop/into_js_function_impls.rs
./core/engine/src/object/internal_methods/string.rs
./core/engine/src/object/internal_methods/mod.rs
./core/engine/src/object/internal_methods/immu...<truncated>
returncode: 0

#### Step 4
Action:
- tool: bash
  command: cat ./core/engine/src/context/mod.rs
- tool: bash
  command: cat ./core/engine/src/lib.rs
Observation:
returncode: 0
returncode: 0

#### Step 5
Action:
- tool: bash
  command: cat ./core/engine/src/script.rs
- tool: bash
  command: cat ./core/engine/src/module.rs
Observation:
returncode: 0
output:
//! Boa's implementation of ECMAScript's Scripts.
//!
//! This module contains the [`Script`] type, which represents a [**Script Record**][script].
//!
//! More information:
//!  - [ECMAScript reference][spec]
//!
//! [spec]: https://tc39.es/ecma262/#sec-scripts
//! [script]: https://tc39.es/ecma262/#sec-script-records

use std::path::{Path, PathBuf};

use rustc_hash::FxHashMap;

use boa_gc::{Finalize, Gc, GcRefCell, Trace};
use boa_parser::{Parser, Source, source::ReadChar...<truncated>
returncode: 1
output:
cat: ./core/engine/src/module.rs: No such file or directory


#### Step 6
Action:
- tool: bash
  command: find ./core/engine -name "mod*.rs" -path "*/module/*" -o -name "module*.rs" | sort
- tool: bash
  command: ls -la ./core/engine/src/module/
Observation:
returncode: 0
output:
./core/engine/src/bytecompiler/module.rs
./core/engine/src/environments/runtime/declarative/module.rs
./core/engine/src/module/loader/mod.rs
./core/engine/src/module/mod.rs
./core/engine/tests/module.rs

returncode: 0
output:
total 184
drwxr-xr-x  3 root root  4096 Jun 11 06:28 .
drwxr-xr-x 17 root root  4096 Jun 11 06:28 ..
drwxr-xr-x  2 root root  4096 Jun 11 06:28 loader
-rw-r--r--  1 root root 33694 Jun 11 06:28 mod.rs
-rw-r--r--  1 root root 19692 Jun 11 06:28 namespace.rs
-rw-r--r--  1 root root 94686 Jun 11 06:28 source.rs
-rw-r--r--  1 root root 17208 Jun 11 06:28 synthetic.rs


#### Step 7
Action:
- tool: bash
  command: head -200 ./core/engine/src/module/mod.rs
- tool: bash
  command: cat ./core/engine/src/job.rs
Observation:
returncode: 0
output:
//! Boa's implementation of the ECMAScript's module system.
//!
//! This module contains the [`Module`] type, which represents an [**Abstract Module Record**][module],
//! a [`ModuleLoader`] trait for custom module loader implementations, and [`SimpleModuleLoader`],
//! the default `ModuleLoader` for [`Context`] which can be used for most simple usecases.
//!
//! Every module roughly follows the same lifecycle:
//! - Parse using [`Module::parse`].
//! - Load all its depende...<truncated>
returncode: 0

#### Step 8
Action:
- tool: bash
  command: sed -n '150,350p' ./core/engine/src/context/mod.rs
- tool: bash
  command: sed -n '350,550p' ./core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
        #[cfg(feature = "intl")]
        debug.field("intl_provider", &self.intl_provider);

        // TODO: Support TimeZoneProvider debug names
        #[cfg(feature = "temporal")]
        debug.field("timezone_provider", &"TimeZoneProvider");

        debug.finish_non_exhaustive()
    }
}

impl Drop for Context {
    fn drop(&mut self) {
        if !self.can_block {
            CANNOT_BLOCK_COUNTER.set(CANNOT_BLOCK_COUNTER.get() - 1);
        }
    }
}

impl Default for...<truncated>
returncode: 0
output:
    ///
    /// impl Class for MyClass {
    ///    // ...
    /// }
    ///
    /// context.register_global_class::<MyClass>()?;
    /// ```
    pub fn register_global_class<C: Class>(&mut self) -> JsResult<()> {
        if self.realm().has_class::<C>() {
            return Err(JsNativeError::typ()
                .with_message("cannot register a class twice")
                .into());
        }

        let mut class_builder = ClassBuilder::new::<C>(self);
...<truncated>

#### Step 9
Action:
- tool: bash
  command: sed -n '550,750p' ./core/engine/src/context/mod.rs
- tool: bash
  command: grep -n "pub fn" ./core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
    #[inline]
    #[must_use]
    pub fn host_hooks(&self) -> Rc<dyn HostHooks> {
        self.host_hooks.clone()
    }

    /// Gets the internal clock.
    #[inline]
    #[must_use]
    pub fn clock(&self) -> &dyn Clock {
        self.clock.as_ref()
    }

    /// Gets the current job executor, or `None` if the current job executor
    /// is not a `T`.
    #[inline]
    #[must_use]
    pub fn downcast_job_executor<T: 'static>(&self) -> Option<Rc<T>> {
        Rc::downcas...<truncated>
returncode: 0
output:
182:    pub fn builder() -> ContextBuilder {
204:    pub fn eval<R: ReadChar>(&mut self, src: Source<'_, R>) -> JsResult<JsValue> {
209:    pub fn optimize_statement_list(
251:    pub fn register_global_property<K, V>(
285:    pub fn register_global_callable(
318:    pub fn register_global_builtin_callable(
357:    pub fn register_global_class<C: Class>(&mut self) -> JsResult<()> {
404:    pub fn unregister_global_class<C: Class>(&mut self) -> JsResult<Option...<truncated>

#### Step 10
Action:
- tool: bash
  command: grep -n "pub fn" ./core/engine/src/module/mod.rs
- tool: bash
  command: grep -n "pub fn\|pub async fn" ./core/engine/src/module/source.rs
Observation:
returncode: 0
output:
75:    pub fn new(key: JsString, value: JsString) -> Self {
81:    pub fn key(&self) -> &JsString {
87:    pub fn value(&self) -> &JsString {
108:    pub fn new(specifier: JsString, mut attributes: Box<[ImportAttribute]>) -> Self {
119:    pub fn from_specifier(specifier: JsString) -> Self {
148:    pub fn specifier(&self) -> &JsString {
154:    pub fn attributes(&self) -> &[ImportAttribute] {
160:    pub fn get_attribute(&self, key: &str) -> Option<&JsString> {
272:    pub...<truncated>
returncode: 1

#### Step 11
Action:
- tool: bash
  command: sed -n '581,700p' ./core/engine/src/module/mod.rs
- tool: bash
  command: sed -n '250,330p' ./core/engine/src/module/mod.rs
Observation:
returncode: 0
output:
    pub fn evaluate(&self, context: &mut Context) -> JsResult<JsPromise> {
        match self.kind() {
            ModuleKind::SourceText(src) => src.evaluate(self, context),
            ModuleKind::Synthetic(synth) => synth.evaluate(self, context),
        }
    }

    /// Abstract operation [`InnerModuleLinking ( module, stack, index )`][spec].
    ///
    /// [spec]: https://tc39.es/ecma262/#sec-InnerModuleLinking
    fn inner_evaluate(
        &self,
        stack: &mut...<truncated>
returncode: 0
output:
}

#[derive(Debug, Clone)]
struct GraphLoadingState {
    capability: PromiseCapability,
    loading: Cell<bool>,
    pending_modules: Cell<usize>,
    visited: RefCell<HashSet<Module>>,
}

#[derive(Debug, Clone, Copy)]
pub(crate) enum ResolveExportError {
    NotFound,
    Ambiguous,
}

impl Module {
    /// Abstract operation [`ParseModule ( sourceText, realm, hostDefined )`][spec].
    ///
    /// Parses the provided `src` as an ECMAScript module, returnin...<truncated>

#### Step 12
Action:
- tool: bash
  command: cat ./core/engine/Cargo.toml
- tool: bash
  command: grep -n "pub use" ./core/engine/src/lib.rs | head -20
Observation:
returncode: 0
output:
[package]
name = "boa_engine"
keywords = ["javascript", "js", "compiler", "lexer", "parser"]
categories = ["parser-implementations", "compilers"]
readme = "../../README.md"
description.workspace = true
version.workspace = true
edition.workspace = true
authors.workspace = true
license.workspace = true
repository.workspace = true
rust-version.workspace = true

[features]
default = ["float16", "xsum", "temporal"]

embedded_lz4 = ["boa_macros/embedded_lz4", "lz4_flex"]

# Repla...<truncated>
returncode: 0
output:
80:pub use boa_ast as ast;
81:pub use boa_gc as gc;
82:pub use boa_interner as interner;
83:pub use boa_parser as parser;
112:pub use spanned_source_text::SpannedSourceText;
119:    pub use crate::{
133:    pub use boa_gc::{Finalize, Trace};
134:    pub use boa_macros::{JsData, js_str};
135:    pub use boa_parser::Source;
139:pub use boa_macros::{boa_class, boa_module, embed_module_inner as __embed_module_inner};
146:pub use prelude::*;
149:pub use boa_parser...<truncated>

#### Step 13
Action:
- tool: bash
  command: ls -la ./core/engine/src/tests/
- tool: bash
  command: cat ./core/engine/src/tests/job.rs
Observation:
returncode: 0
output:
total 128
drwxr-xr-x  3 root root  4096 Jun 11 06:28 .
drwxr-xr-x 17 root root  4096 Jun 11 06:28 ..
-rw-r--r--  1 root root  3827 Jun 11 06:28 async_generator.rs
-rw-r--r--  1 root root  5590 Jun 11 06:28 class.rs
drwxr-xr-x  2 root root  4096 Jun 11 06:28 control_flow
-rw-r--r--  1 root root  2559 Jun 11 06:28 env.rs
-rw-r--r--  1 root root  5118 Jun 11 06:28 function.rs
-rw-r--r--  1 root root  7863 Jun 11 06:28 generators.rs
-rw-r--r--  1 root root  6481 Jun 11 06:28 it...<truncated>
returncode: 0
output:
use std::{
    cell::{Cell, RefCell},
    pin::pin,
    rc::Rc,
};

use futures_lite::future;

use crate::{
    JsValue, TestAction,
    context::{ContextBuilder, time::FixedClock},
    job::{GenericJob, JobExecutor, NativeAsyncJob, SimpleJobExecutor},
    run_test_actions_with,
};

#[test]
fn test_async_job_not_blocking_event_loop() {
    let clock = Rc::new(FixedClock::default());
    let context = &mut ContextBuilder::default()
        .clock(clock.clone()...<truncated>

#### Step 14
Action:
- tool: bash
  command: cat > ./core/engine/src/evaluation_handle.rs << 'EOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.

use std::cell::Cell;
use std::rc::Rc;

use crate::{Context, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this handle (or an ancestor) has been cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if set.
    reason: std::cell::RefCell<Option<JsValue>>,
    /// The parent handle, if any.
    parent: Option<EvaluationHandle>,
}

/// A handle that can be used to cancel evaluation across nested evaluations,
/// module phases, and queued jobs.
///
/// ...<truncated>
Observation:
returncode: 0

### Minimal Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: find . -type f -name "*.rs" | head -50
- tool: bash
  command: ls -la
Observation:
returncode: 0
output:
./target/debug/build/serde_core-15f86ebe79c73b6f/out/private.rs
./target/debug/build/serde-ad0ce9851116ebb1/out/private.rs
./target/debug/build/thiserror-61914d7c68dd9648/out/private.rs
./cli/src/helper.rs
./cli/src/main.rs
./cli/src/executor.rs
./cli/src/debug/limits.rs
./cli/src/debug/function.rs
./cli/src/debug/string.rs
./cli/src/debug/mod.rs
./cli/src/debug/gc.rs
./cli/src/debug/realm.rs
./cli/src/debug/shape.rs
./cli/src/debug/optimizer.rs
./cli/src/debug/object.rs
./...<truncated>
returncode: 0
output:
total 508
drwxrwxrwx  1 root root   4096 Jun 11 06:29 .
drwxr-xr-x  1 root root   4096 Jun 21 02:15 ..
drwxr-xr-x  2 root root   4096 Jun 11 06:28 .cargo
drwxr-xr-x  2 root root   4096 Jun 11 06:28 .config
-rw-r--r--  1 root root    317 Jun 11 06:28 .editorconfig
drwxr-xr-x  1 root root   4096 Jun 11 19:40 .git
-rw-r--r--  1 root root    781 Jun 11 06:28 .gitattributes
drwxr-xr-x  4 root root   4096 Jun 11 06:28 .github
-rw-r--r--  1 root root    522 Jun 11 0...<truncated>

#### Step 2
Action:
- tool: bash
  command: find . -type f -name "*.rs" -not -path "./target/*" | sort
Observation:
returncode: 0

#### Step 3
Action:
- tool: bash
  command: find ./core -name "*.rs" -not -path "./target/*" | xargs grep -l "Context" 2>/dev/null | head -20
- tool: bash
  command: find ./core/engine/src -name "*.rs" | sort
Observation:
returncode: 0
output:
./core/ast/src/scope_analyzer.rs
./core/ast/src/scope.rs
./core/engine/src/symbol.rs
./core/engine/src/context/mod.rs
./core/engine/src/context/hooks.rs
./core/engine/src/try_into_js_result_impls.rs
./core/engine/src/interop/mod.rs
./core/engine/src/interop/into_js_arguments.rs
./core/engine/src/interop/into_js_function_impls.rs
./core/engine/src/object/internal_methods/string.rs
./core/engine/src/object/internal_methods/mod.rs
./core/engine/src/object/internal_methods/immu...<truncated>
returncode: 0

#### Step 4
Action:
- tool: bash
  command: cat ./core/engine/src/context/mod.rs
- tool: bash
  command: cat ./core/engine/src/lib.rs
Observation:
returncode: 0
returncode: 0

#### Step 5
Action:
- tool: bash
  command: cat ./core/engine/src/script.rs
- tool: bash
  command: cat ./core/engine/src/module.rs
Observation:
returncode: 0
output:
//! Boa's implementation of ECMAScript's Scripts.
//!
//! This module contains the [`Script`] type, which represents a [**Script Record**][script].
//!
//! More information:
//!  - [ECMAScript reference][spec]
//!
//! [spec]: https://tc39.es/ecma262/#sec-scripts
//! [script]: https://tc39.es/ecma262/#sec-script-records

use std::path::{Path, PathBuf};

use rustc_hash::FxHashMap;

use boa_gc::{Finalize, Gc, GcRefCell, Trace};
use boa_parser::{Parser, Source, source::ReadChar...<truncated>
returncode: 1
output:
cat: ./core/engine/src/module.rs: No such file or directory


#### Step 6
Action:
- tool: bash
  command: find ./core/engine -name "mod*.rs" -path "*/module/*" -o -name "module*.rs" | sort
- tool: bash
  command: ls -la ./core/engine/src/module/
Observation:
returncode: 0
output:
./core/engine/src/bytecompiler/module.rs
./core/engine/src/environments/runtime/declarative/module.rs
./core/engine/src/module/loader/mod.rs
./core/engine/src/module/mod.rs
./core/engine/tests/module.rs

returncode: 0
output:
total 184
drwxr-xr-x  3 root root  4096 Jun 11 06:28 .
drwxr-xr-x 17 root root  4096 Jun 11 06:28 ..
drwxr-xr-x  2 root root  4096 Jun 11 06:28 loader
-rw-r--r--  1 root root 33694 Jun 11 06:28 mod.rs
-rw-r--r--  1 root root 19692 Jun 11 06:28 namespace.rs
-rw-r--r--  1 root root 94686 Jun 11 06:28 source.rs
-rw-r--r--  1 root root 17208 Jun 11 06:28 synthetic.rs


#### Step 7
Action:
- tool: bash
  command: head -200 ./core/engine/src/module/mod.rs
- tool: bash
  command: cat ./core/engine/src/job.rs
Observation:
returncode: 0
output:
//! Boa's implementation of the ECMAScript's module system.
//!
//! This module contains the [`Module`] type, which represents an [**Abstract Module Record**][module],
//! a [`ModuleLoader`] trait for custom module loader implementations, and [`SimpleModuleLoader`],
//! the default `ModuleLoader` for [`Context`] which can be used for most simple usecases.
//!
//! Every module roughly follows the same lifecycle:
//! - Parse using [`Module::parse`].
//! - Load all its depende...<truncated>
returncode: 0

#### Step 8
Action:
- tool: bash
  command: sed -n '150,350p' ./core/engine/src/context/mod.rs
- tool: bash
  command: sed -n '350,550p' ./core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
        #[cfg(feature = "intl")]
        debug.field("intl_provider", &self.intl_provider);

        // TODO: Support TimeZoneProvider debug names
        #[cfg(feature = "temporal")]
        debug.field("timezone_provider", &"TimeZoneProvider");

        debug.finish_non_exhaustive()
    }
}

impl Drop for Context {
    fn drop(&mut self) {
        if !self.can_block {
            CANNOT_BLOCK_COUNTER.set(CANNOT_BLOCK_COUNTER.get() - 1);
        }
    }
}

impl Default for...<truncated>
returncode: 0
output:
    ///
    /// impl Class for MyClass {
    ///    // ...
    /// }
    ///
    /// context.register_global_class::<MyClass>()?;
    /// ```
    pub fn register_global_class<C: Class>(&mut self) -> JsResult<()> {
        if self.realm().has_class::<C>() {
            return Err(JsNativeError::typ()
                .with_message("cannot register a class twice")
                .into());
        }

        let mut class_builder = ClassBuilder::new::<C>(self);
...<truncated>

#### Step 9
Action:
- tool: bash
  command: sed -n '550,750p' ./core/engine/src/context/mod.rs
- tool: bash
  command: grep -n "pub fn" ./core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
    #[inline]
    #[must_use]
    pub fn host_hooks(&self) -> Rc<dyn HostHooks> {
        self.host_hooks.clone()
    }

    /// Gets the internal clock.
    #[inline]
    #[must_use]
    pub fn clock(&self) -> &dyn Clock {
        self.clock.as_ref()
    }

    /// Gets the current job executor, or `None` if the current job executor
    /// is not a `T`.
    #[inline]
    #[must_use]
    pub fn downcast_job_executor<T: 'static>(&self) -> Option<Rc<T>> {
        Rc::downcas...<truncated>
returncode: 0
output:
182:    pub fn builder() -> ContextBuilder {
204:    pub fn eval<R: ReadChar>(&mut self, src: Source<'_, R>) -> JsResult<JsValue> {
209:    pub fn optimize_statement_list(
251:    pub fn register_global_property<K, V>(
285:    pub fn register_global_callable(
318:    pub fn register_global_builtin_callable(
357:    pub fn register_global_class<C: Class>(&mut self) -> JsResult<()> {
404:    pub fn unregister_global_class<C: Class>(&mut self) -> JsResult<Option...<truncated>

#### Step 10
Action:
- tool: bash
  command: cat > ./core/engine/src/evaluation_handle.rs << 'EOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.

use std::cell::Cell;
use std::rc::Rc;

use crate::{Context, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this handle (or an ancestor) has been cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if set.
    reason: std::cell::RefCell<Option<JsValue>>,
    /// The parent handle, if any.
    parent: Option<EvaluationHandle>,
}

/// A handle that can be used to cancel evaluation across nested evaluations,
/// module phases, and queued jobs.
///
/// ...<truncated>
Observation:
returncode: 0

# Chunk 2

## Graph Contrastive
Phase: read (action steps 15-19 of this trajectory, 5 steps total)
Anchor: step 5 (last successful read in this phase)
Minimal subgraph: [0, 5] (anchor + dependency closure, failed non-explore steps filtered out)
Removable steps (not in closure of anchor 5): [1, 2, 3, 4]. A batching script that skips or merges these would not affect the anchor's outcome.

### Predecessor Context (from before this phase)

#### Earlier step (global 1)

#### Step 1
Action:
- tool: bash
  command: find . -type f -name "*.rs" | head -50
- tool: bash
  command: ls -la
Observation:
returncode: 0
output:
./target/debug/build/serde_core-15f86ebe79c73b6f/out/private.rs
./target/debug/build/serde-ad0ce9851116ebb1/out/private.rs
./target/debug/build/thiserror-61914d7c68dd9648/out/private.rs
./cli/src/helper.rs
./cli/src/main.rs
./cli/src/executor.rs
./cli/src/debug/limits.rs
./cli/src/debug/function.rs
./cli/src/debug/string.rs
./cli/src/debug/mod.rs
./cli/src/debug/gc.rs
./cli/src/debug/realm.rs
./cli/src/debug/shape.rs
./cli/src/debug/optimizer.rs
./cli/src/debug/object.rs
./...<truncated>
returncode: 0
output:
total 508
drwxrwxrwx  1 root root   4096 Jun 11 06:29 .
drwxr-xr-x  1 root root   4096 Jun 21 02:15 ..
drwxr-xr-x  2 root root   4096 Jun 11 06:28 .cargo
drwxr-xr-x  2 root root   4096 Jun 11 06:28 .config
-rw-r--r--  1 root root    317 Jun 11 06:28 .editorconfig
drwxr-xr-x  1 root root   4096 Jun 11 19:40 .git
-rw-r--r--  1 root root    781 Jun 11 06:28 .gitattributes
drwxr-xr-x  4 root root   4096 Jun 11 06:28 .github
-rw-r--r--  1 root root    522 Jun 11 0...<truncated>

#### Earlier step (global 2)

#### Step 1
Action:
- tool: bash
  command: find . -type f -name "*.rs" -not -path "./target/*" | sort
Observation:
returncode: 0

#### Earlier step (global 3)

#### Step 1
Action:
- tool: bash
  command: find ./core -name "*.rs" -not -path "./target/*" | xargs grep -l "Context" 2>/dev/null | head -20
- tool: bash
  command: find ./core/engine/src -name "*.rs" | sort
Observation:
returncode: 0
output:
./core/ast/src/scope_analyzer.rs
./core/ast/src/scope.rs
./core/engine/src/symbol.rs
./core/engine/src/context/mod.rs
./core/engine/src/context/hooks.rs
./core/engine/src/try_into_js_result_impls.rs
./core/engine/src/interop/mod.rs
./core/engine/src/interop/into_js_arguments.rs
./core/engine/src/interop/into_js_function_impls.rs
./core/engine/src/object/internal_methods/string.rs
./core/engine/src/object/internal_methods/mod.rs
./core/engine/src/object/internal_methods/immu...<truncated>
returncode: 0

#### Earlier step (global 4)

#### Step 1
Action:
- tool: bash
  command: cat ./core/engine/src/context/mod.rs
- tool: bash
  command: cat ./core/engine/src/lib.rs
Observation:
returncode: 0
returncode: 0
### Original Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: grep -n "pub enum Job\|pub struct.*Job" ./core/engine/src/job.rs | head -30
- tool: bash
  command: sed -n '1,100p' ./core/engine/src/job.rs
Observation:
returncode: 0
output:
61:pub struct NativeJob {
157:pub struct TimeoutJob {
255:pub struct GenericJob(NativeJob);
296:pub struct NativeAsyncJob {
408:pub struct PromiseJob(NativeJob);
454:pub struct JobCallback {
521:pub enum Job {
612:pub struct IdleJobExecutor;
629:pub struct SimpleJobExecutor {

returncode: 0
output:
//! Boa's API to create and customize `ECMAScript` jobs and job queues.
//!
//! [`Job`] is an ECMAScript [Job], or a closure that runs an `ECMAScript` computation when
//! there's no other computation running. The module defines several type of jobs:
//! - [`PromiseJob`] for Promise related jobs.
//! - [`TimeoutJob`] for jobs that run after a certain amount of time.
//! - [`NativeAsyncJob`] for jobs that support [`Future`].
//! - [`NativeJob`] for generic jobs that aren't r...<truncated>

#### Step 2
Action:
- tool: bash
  command: sed -n '521,600p' ./core/engine/src/job.rs
Observation:
returncode: 0
output:
pub enum Job {
    /// A `Promise`-related job.
    ///
    /// See [`PromiseJob`] for more information.
    PromiseJob(PromiseJob),
    /// A [`Future`]-related job.
    ///
    /// See [`NativeAsyncJob`] for more information.
    AsyncJob(NativeAsyncJob),
    /// A generic job that is to be executed after a number of milliseconds.
    ///
    /// See [`TimeoutJob`] for more information.
    TimeoutJob(TimeoutJob),
    /// A generic job.
    ///
    /// See [`GenericJob`] for more information.
    GenericJob(GenericJob),
}

impl From<NativeAsyncJob> for Job {
    fn from(native_async_job: NativeAsyncJob) -> Self {
        Job::AsyncJob(native_async_job)
    }
}

impl From<PromiseJob> for Job {
    fn from(promise_job: PromiseJob) -> Self {
        Job::PromiseJob(promise_job)
    }
}

impl From<TimeoutJob> for Job {
    fn from(job: TimeoutJob) -> Self {
        Job::TimeoutJob(job)
    }
}

impl From<GenericJob> for Job {
    fn from(job: GenericJob) -> Self {
 ...<truncated>

#### Step 3
Action:
- tool: bash
  command: sed -n '255,295p' ./core/engine/src/job.rs
Observation:
returncode: 0
output:
pub struct GenericJob(NativeJob);

impl Debug for GenericJob {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("GenericJob").finish_non_exhaustive()
    }
}

impl GenericJob {
    /// Creates a new `GenericJob` from a closure and an execution realm.
    pub fn new<F>(f: F, realm: Realm) -> Self
    where
        F: FnOnce(&mut Context) -> JsResult<JsValue> + 'static,
    {
        Self(NativeJob::with_realm(f, realm))
    }

    /// Gets a reference to the execution realm of the job.
    #[must_use]
    pub const fn realm(&self) -> &Realm {
        self.0
            .realm
            .as_ref()
            .expect("all generic jobs must have an execution realm")
    }

    /// Calls the `GenericJob` with the specified [`Context`], setting the execution
    /// context to the job's realm before calling the inner closure, and resets it after execution.
    pub fn call(self, context: &mut Context) -> JsResult<JsValue> ...<truncated>

#### Step 4
Action:
- tool: bash
  command: sed -n '408,520p' ./core/engine/src/job.rs
Observation:
returncode: 0
output:
pub struct PromiseJob(NativeJob);

impl Debug for PromiseJob {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PromiseJob").finish_non_exhaustive()
    }
}

impl PromiseJob {
    /// Creates a new `PromiseJob` from a closure.
    pub fn new<F>(f: F) -> Self
    where
        F: FnOnce(&mut Context) -> JsResult<JsValue> + 'static,
    {
        Self(NativeJob::new(f))
    }

    /// Creates a new `PromiseJob` from a closure and an execution realm.
    pub fn with_realm<F>(f: F, realm: Realm) -> Self
    where
        F: FnOnce(&mut Context) -> JsResult<JsValue> + 'static,
    {
        Self(NativeJob::with_realm(f, realm))
    }

    /// Gets a reference to the execution realm of the `PromiseJob`.
    #[must_use]
    pub const fn realm(&self) -> Option<&Realm> {
        self.0.realm()
    }

    /// Calls the `PromiseJob` with the specified [`Context`].
    ///
    /// # Note
    ///
    /// If the job has an executi...<truncated>

#### Step 5
Action:
- tool: bash
  command: grep -n "fn run\|fn run_async" ./core/engine/src/vm/mod.rs | head -20
Observation:
returncode: 0
output:
867:    pub(crate) async fn run_async_with_budget(&mut self, budget: u32) -> CompletionRecord {
902:    pub(crate) fn run(&mut self) -> CompletionRecord {


### Minimal Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: grep -n "fn run\|fn run_async" ./core/engine/src/vm/mod.rs | head -20
Observation:
returncode: 0
output:
867:    pub(crate) async fn run_async_with_budget(&mut self, budget: u32) -> CompletionRecord {
902:    pub(crate) fn run(&mut self) -> CompletionRecord {


# Chunk 3

## Graph Contrastive
Phase: explore (action steps 20-30 of this trajectory, 11 steps total)
Anchor: step 9 (last successful write in this phase)
Minimal subgraph: [0, 4, 5, 6, 7, 8, 9] (anchor + dependency closure, failed non-explore steps filtered out)
Removable steps (not in closure of anchor 9): [1, 2, 3, 10, 11]. A batching script that skips or merges these would not affect the anchor's outcome.

### Predecessor Context (from before this phase)

#### Earlier step (global 15)

#### Step 1
Action:
- tool: bash
  command: grep -n "pub enum Job\|pub struct.*Job" ./core/engine/src/job.rs | head -30
- tool: bash
  command: sed -n '1,100p' ./core/engine/src/job.rs
Observation:
returncode: 0
output:
61:pub struct NativeJob {
157:pub struct TimeoutJob {
255:pub struct GenericJob(NativeJob);
296:pub struct NativeAsyncJob {
408:pub struct PromiseJob(NativeJob);
454:pub struct JobCallback {
521:pub enum Job {
612:pub struct IdleJobExecutor;
629:pub struct SimpleJobExecutor {

returncode: 0
output:
//! Boa's API to create and customize `ECMAScript` jobs and job queues.
//!
//! [`Job`] is an ECMAScript [Job], or a closure that runs an `ECMAScript` computation when
//! there's no other computation running. The module defines several type of jobs:
//! - [`PromiseJob`] for Promise related jobs.
//! - [`TimeoutJob`] for jobs that run after a certain amount of time.
//! - [`NativeAsyncJob`] for jobs that support [`Future`].
//! - [`NativeJob`] for generic jobs that aren't r...<truncated>

#### Earlier step (global 16)

#### Step 1
Action:
- tool: bash
  command: sed -n '521,600p' ./core/engine/src/job.rs
Observation:
returncode: 0
output:
pub enum Job {
    /// A `Promise`-related job.
    ///
    /// See [`PromiseJob`] for more information.
    PromiseJob(PromiseJob),
    /// A [`Future`]-related job.
    ///
    /// See [`NativeAsyncJob`] for more information.
    AsyncJob(NativeAsyncJob),
    /// A generic job that is to be executed after a number of milliseconds.
    ///
    /// See [`TimeoutJob`] for more information.
    TimeoutJob(TimeoutJob),
    /// A generic job.
    ///
    /// See [`GenericJob`] for more information.
    GenericJob(GenericJob),
}

impl From<NativeAsyncJob> for Job {
    fn from(native_async_job: NativeAsyncJob) -> Self {
        Job::AsyncJob(native_async_job)
    }
}

impl From<PromiseJob> for Job {
    fn from(promise_job: PromiseJob) -> Self {
        Job::PromiseJob(promise_job)
    }
}

impl From<TimeoutJob> for Job {
    fn from(job: TimeoutJob) -> Self {
        Job::TimeoutJob(job)
    }
}

impl From<GenericJob> for Job {
    fn from(job: GenericJob) -> Self {
 ...<truncated>

#### Earlier step (global 17)

#### Step 1
Action:
- tool: bash
  command: sed -n '255,295p' ./core/engine/src/job.rs
Observation:
returncode: 0
output:
pub struct GenericJob(NativeJob);

impl Debug for GenericJob {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("GenericJob").finish_non_exhaustive()
    }
}

impl GenericJob {
    /// Creates a new `GenericJob` from a closure and an execution realm.
    pub fn new<F>(f: F, realm: Realm) -> Self
    where
        F: FnOnce(&mut Context) -> JsResult<JsValue> + 'static,
    {
        Self(NativeJob::with_realm(f, realm))
    }

    /// Gets a reference to the execution realm of the job.
    #[must_use]
    pub const fn realm(&self) -> &Realm {
        self.0
            .realm
            .as_ref()
            .expect("all generic jobs must have an execution realm")
    }

    /// Calls the `GenericJob` with the specified [`Context`], setting the execution
    /// context to the job's realm before calling the inner closure, and resets it after execution.
    pub fn call(self, context: &mut Context) -> JsResult<JsValue> ...<truncated>

#### Earlier step (global 18)

#### Step 1
Action:
- tool: bash
  command: sed -n '408,520p' ./core/engine/src/job.rs
Observation:
returncode: 0
output:
pub struct PromiseJob(NativeJob);

impl Debug for PromiseJob {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PromiseJob").finish_non_exhaustive()
    }
}

impl PromiseJob {
    /// Creates a new `PromiseJob` from a closure.
    pub fn new<F>(f: F) -> Self
    where
        F: FnOnce(&mut Context) -> JsResult<JsValue> + 'static,
    {
        Self(NativeJob::new(f))
    }

    /// Creates a new `PromiseJob` from a closure and an execution realm.
    pub fn with_realm<F>(f: F, realm: Realm) -> Self
    where
        F: FnOnce(&mut Context) -> JsResult<JsValue> + 'static,
    {
        Self(NativeJob::with_realm(f, realm))
    }

    /// Gets a reference to the execution realm of the `PromiseJob`.
    #[must_use]
    pub const fn realm(&self) -> Option<&Realm> {
        self.0.realm()
    }

    /// Calls the `PromiseJob` with the specified [`Context`].
    ///
    /// # Note
    ///
    /// If the job has an executi...<truncated>

#### Earlier step (global 19)

#### Step 1
Action:
- tool: bash
  command: grep -n "fn run\|fn run_async" ./core/engine/src/vm/mod.rs | head -20
Observation:
returncode: 0
output:
867:    pub(crate) async fn run_async_with_budget(&mut self, budget: u32) -> CompletionRecord {
902:    pub(crate) fn run(&mut self) -> CompletionRecord {

### Original Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: cd /workspace && git branch -a
- tool: bash
  command: cd /workspace && git status
Observation:
returncode: 2
output:
/bin/sh: 1: cd: can't cd to /workspace

returncode: 2
output:
/bin/sh: 1: cd: can't cd to /workspace


#### Step 2
Action:
- tool: bash
  command: pwd && ls -la .git 2>/dev/null; git branch -a 2>/dev/null || echo "not a git repo"
Observation:
returncode: 0
output:
/app
total 160
drwxr-xr-x 1 root root   4096 Jun 11 19:40 .
drwxrwxrwx 1 root root   4096 Jun 11 06:29 ..
-rw-r--r-- 1 root root     21 Jun 11 06:28 HEAD
drwxr-xr-x 2 root root   4096 Jun 11 06:28 branches
-rw-r--r-- 1 root root    115 Jun 11 19:40 config
-rw-r--r-- 1 root root     73 Jun 11 06:28 description
drwxr-xr-x 2 root root   4096 Jun 11 06:28 hooks
-rw-r--r-- 1 root root 108756 Jun 11 06:28 index
drwxr-xr-x 2 root root   4096 Jun 11 06:28 info
drwxr-xr-x 3 root root   4096 Jun 11 06:28 logs
drwxr-xr-x 4 root root   4096 Jun 11 06:28 objects
-rw-r--r-- 1 root root   1369 Jun 11 06:28 packed-refs
drwxr-xr-x 5 root root   4096 Jun 11 06:28 refs
* main


#### Step 3
Action:
- tool: bash
  command: cd /app && git checkout -b evaluation-handles
Observation:
returncode: 0
output:
Switched to a new branch 'evaluation-handles'


#### Step 4
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0

#### Step 5
Action:
- tool: bash
  command: sed -n '521,620p' /app/core/engine/src/job.rs
Observation:
returncode: 0
output:
pub enum Job {
    /// A `Promise`-related job.
    ///
    /// See [`PromiseJob`] for more information.
    PromiseJob(PromiseJob),
    /// A [`Future`]-related job.
    ///
    /// See [`NativeAsyncJob`] for more information.
    AsyncJob(NativeAsyncJob),
    /// A generic job that is to be executed after a number of milliseconds.
    ///
    /// See [`TimeoutJob`] for more information.
    TimeoutJob(TimeoutJob),
    /// A generic job.
    ///
    /// See [`GenericJob`] for more information.
    GenericJob(GenericJob),
}

impl From<NativeAsyncJob> for Job {
    fn from(native_async_job: NativeAsyncJob) -> Self {
        Job::AsyncJob(native_async_job)
    }
}

impl From<PromiseJob> for Job {
    fn from(promise_job: PromiseJob) -> Self {
        Job::PromiseJob(promise_job)
    }
}

impl From<TimeoutJob> for Job {
    fn from(job: TimeoutJob) -> Self {
        Job::TimeoutJob(job)
    }
}

impl From<GenericJob> for Job {
    fn from(job: GenericJob) -> Self {
 ...<truncated>

#### Step 6
Action:
- tool: bash
  command: rm /app/core/engine/src/evaluation_handle.rs
Observation:
returncode: 0

#### Step 7
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0

#### Step 8
Action:
- tool: bash
  command: grep -n "pub mod" /app/core/engine/src/lib.rs
Observation:
returncode: 0
output:
85:pub mod bigint;
86:pub mod builtins;
87:pub mod bytecompiler;
88:pub mod class;
89:pub mod context;
90:pub mod environments;
91:pub mod error;
92:pub mod interop;
93:pub mod job;
94:pub mod module;
95:pub mod native_function;
96:pub mod object;
98:pub mod optimizer;
99:pub mod property;
100:pub mod realm;
101:pub mod script;
102:pub mod string;
103:pub mod symbol;
104:pub mod value;
105:pub mod vm;
118:pub mod prelude {


#### Step 9
Action:
- tool: bash
  command: cd /app && sed -i '91a pub mod evaluation_handle;' core/engine/src/lib.rs
Observation:
returncode: 0

#### Step 10
Action:
- tool: bash
  command: sed -n '62,120p' /app/core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
/// use boa_engine::{
///     Context, Source, js_string,
///     object::ObjectInitializer,
///     property::{Attribute, PropertyDescriptor},
/// };
///
/// let script = r#"
///     function test(arg1) {
///         if(arg1 != null) {
///             return arg1.x;
///         }
///         return 112233;
///     }
/// "#;
///
/// let mut context = Context::default();
///
/// // Populate the script definition to the context.
/// context.eval(Source::from_bytes(script)).unwrap();
///
/// // Create an object that can be used in eval calls.
/// let arg = ObjectInitializer::new(&mut context)
///     .property(js_string!("x"), 12, Attribute::READONLY)
///     .build();
/// context
///     .register_global_property(js_string!("arg"), arg, Attribute::all())
///     .expect("property shouldn't exist");
///
/// let value = context.eval(Source::from_bytes("test(arg)")).unwrap();
///
/// assert_eq!(value.as_number(), Some(12.0))
/// ```
pub struct Context {
    /// String ...<truncated>

#### Step 11
Action:
- tool: bash
  command: sed -n '110,150p' /app/core/engine/src/context/mod.rs
Observation:
returncode: 0
output:

    #[cfg(any(feature = "temporal", feature = "intl"))]
    timezone_provider: Box<dyn TimeZoneProvider>,

    /// Intl data provider.
    #[cfg(feature = "intl")]
    intl_provider: icu::IntlProvider,

    host_hooks: Rc<dyn HostHooks>,

    clock: Rc<dyn Clock>,

    job_executor: Rc<dyn JobExecutor>,

    module_loader: Rc<dyn DynModuleLoader>,

    optimizer_options: OptimizerOptions,
    root_shape: RootShape,

    /// Unique identifier for each parser instance used during the context lifetime.
    parser_identifier: u32,

    data: HostDefined,
}

impl std::fmt::Debug for Context {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut debug = f.debug_struct("Context");

        debug
            .field("realm", &self.vm.frame().realm)
            .field("interner", &self.interner)
            .field("vm", &self.vm)
            .field("strict", &self.strict)
            .field("job_executor", &"JobExecutor")
            .fi...<truncated>

### Minimal Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0

#### Step 2
Action:
- tool: bash
  command: sed -n '521,620p' /app/core/engine/src/job.rs
Observation:
returncode: 0
output:
pub enum Job {
    /// A `Promise`-related job.
    ///
    /// See [`PromiseJob`] for more information.
    PromiseJob(PromiseJob),
    /// A [`Future`]-related job.
    ///
    /// See [`NativeAsyncJob`] for more information.
    AsyncJob(NativeAsyncJob),
    /// A generic job that is to be executed after a number of milliseconds.
    ///
    /// See [`TimeoutJob`] for more information.
    TimeoutJob(TimeoutJob),
    /// A generic job.
    ///
    /// See [`GenericJob`] for more information.
    GenericJob(GenericJob),
}

impl From<NativeAsyncJob> for Job {
    fn from(native_async_job: NativeAsyncJob) -> Self {
        Job::AsyncJob(native_async_job)
    }
}

impl From<PromiseJob> for Job {
    fn from(promise_job: PromiseJob) -> Self {
        Job::PromiseJob(promise_job)
    }
}

impl From<TimeoutJob> for Job {
    fn from(job: TimeoutJob) -> Self {
        Job::TimeoutJob(job)
    }
}

impl From<GenericJob> for Job {
    fn from(job: GenericJob) -> Self {
 ...<truncated>

#### Step 3
Action:
- tool: bash
  command: rm /app/core/engine/src/evaluation_handle.rs
Observation:
returncode: 0

#### Step 4
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0

#### Step 5
Action:
- tool: bash
  command: grep -n "pub mod" /app/core/engine/src/lib.rs
Observation:
returncode: 0
output:
85:pub mod bigint;
86:pub mod builtins;
87:pub mod bytecompiler;
88:pub mod class;
89:pub mod context;
90:pub mod environments;
91:pub mod error;
92:pub mod interop;
93:pub mod job;
94:pub mod module;
95:pub mod native_function;
96:pub mod object;
98:pub mod optimizer;
99:pub mod property;
100:pub mod realm;
101:pub mod script;
102:pub mod string;
103:pub mod symbol;
104:pub mod value;
105:pub mod vm;
118:pub mod prelude {


#### Step 6
Action:
- tool: bash
  command: cd /app && sed -i '91a pub mod evaluation_handle;' core/engine/src/lib.rs
Observation:
returncode: 0

# Chunk 4

## Graph Contrastive
Phase: write (action steps 31-39 of this trajectory, 9 steps total)
Anchor: step 9 (last successful write in this phase)
Minimal subgraph: [0, 1, 3, 6, 9] (anchor + dependency closure, failed non-explore steps filtered out)
Removable steps (not in closure of anchor 9): [2, 4, 5, 7, 8]. A batching script that skips or merges these would not affect the anchor's outcome.

### Predecessor Context (from before this phase)

#### Earlier step (global 24)

#### Step 1
Action:
- tool: bash
  command: sed -n '521,620p' /app/core/engine/src/job.rs
Observation:
returncode: 0
output:
pub enum Job {
    /// A `Promise`-related job.
    ///
    /// See [`PromiseJob`] for more information.
    PromiseJob(PromiseJob),
    /// A [`Future`]-related job.
    ///
    /// See [`NativeAsyncJob`] for more information.
    AsyncJob(NativeAsyncJob),
    /// A generic job that is to be executed after a number of milliseconds.
    ///
    /// See [`TimeoutJob`] for more information.
    TimeoutJob(TimeoutJob),
    /// A generic job.
    ///
    /// See [`GenericJob`] for more information.
    GenericJob(GenericJob),
}

impl From<NativeAsyncJob> for Job {
    fn from(native_async_job: NativeAsyncJob) -> Self {
        Job::AsyncJob(native_async_job)
    }
}

impl From<PromiseJob> for Job {
    fn from(promise_job: PromiseJob) -> Self {
        Job::PromiseJob(promise_job)
    }
}

impl From<TimeoutJob> for Job {
    fn from(job: TimeoutJob) -> Self {
        Job::TimeoutJob(job)
    }
}

impl From<GenericJob> for Job {
    fn from(job: GenericJob) -> Self {
 ...<truncated>

#### Earlier step (global 25)

#### Step 1
Action:
- tool: bash
  command: rm /app/core/engine/src/evaluation_handle.rs
Observation:
returncode: 0

#### Earlier step (global 26)

#### Step 1
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0

#### Earlier step (global 27)

#### Step 1
Action:
- tool: bash
  command: grep -n "pub mod" /app/core/engine/src/lib.rs
Observation:
returncode: 0
output:
85:pub mod bigint;
86:pub mod builtins;
87:pub mod bytecompiler;
88:pub mod class;
89:pub mod context;
90:pub mod environments;
91:pub mod error;
92:pub mod interop;
93:pub mod job;
94:pub mod module;
95:pub mod native_function;
96:pub mod object;
98:pub mod optimizer;
99:pub mod property;
100:pub mod realm;
101:pub mod script;
102:pub mod string;
103:pub mod symbol;
104:pub mod value;
105:pub mod vm;
118:pub mod prelude {


#### Earlier step (global 28)

#### Step 1
Action:
- tool: bash
  command: cd /app && sed -i '91a pub mod evaluation_handle;' core/engine/src/lib.rs
Observation:
returncode: 0
### Original Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: cd /app && sed -i 's/use crate::job::Job;/use crate::evaluation_handle::EvaluationHandle;\nuse crate::job::Job;/' core/engine/src/context/mod.rs
Observation:
returncode: 0

#### Step 2
Action:
- tool: bash
  command: cd /app && sed -i 's/    data: HostDefined,/    data: HostDefined,\n\n    /// The currently active evaluation handle, if any.\n    current_eval_handle: Option<EvaluationHandle>,/' core/engine/src/context/mod.rs
Observation:
returncode: 1
output:
sed: -e expression #1, char 57: unknown option to `s'


#### Step 3
Action:
- tool: bash
  command: cd /app && sed -i 's/    data: HostDefined,/    data: HostDefined,\n\n    \/\/\/ The currently active evaluation handle, if any.\n    current_eval_handle: Option<EvaluationHandle>,/' core/engine/src/context/mod.rs
Observation:
returncode: 0

#### Step 4
Action:
- tool: bash
  command: grep -n "let mut context = Context {" /app/core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
1224:        let mut context = Context {


#### Step 5
Action:
- tool: bash
  command: sed -n '1224,1265p' /app/core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
        let mut context = Context {
            interner: self.interner.unwrap_or_default(),
            vm,
            strict: false,
            #[cfg(feature = "temporal")]
            timezone_provider: if let Some(provider) = self.timezone_provider {
                provider
            } else {
                Box::new(ZeroCompiledTzdbProvider::default())
            },
            #[cfg(feature = "intl")]
            intl_provider: if let Some(icu) = self.icu {
                icu
            } else {
                cfg_if::cfg_if! {
                    if #[cfg(feature = "intl_bundled")] {
                        icu::IntlProvider::try_new_buffer(boa_icu_provider::buffer())
                    } else {
                        return Err(JsNativeError::typ()
                            .with_message("missing Intl provider for context")
                            .into()
                        );
                    }
                }
            },
   ...<truncated>

#### Step 6
Action:
- tool: bash
  command: cd /app && sed -i 's/            data: HostDefined::default(),/            data: HostDefined::default(),\n            current_eval_handle: None,/' core/engine/src/context/mod.rs
Observation:
returncode: 0

#### Step 7
Action:
- tool: bash
  command: grep -n "pub fn enqueue_job\|pub fn run_jobs" /app/core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
496:    pub fn enqueue_job(&mut self, job: Job) {
502:    pub fn run_jobs(&mut self) -> JsResult<()> {


#### Step 8
Action:
- tool: bash
  command: sed -n '496,515p' /app/core/engine/src/context/mod.rs
Observation:
returncode: 0
output:
    pub fn enqueue_job(&mut self, job: Job) {
        self.job_executor().enqueue_job(job, self);
    }

    /// Runs all the jobs with the provided job executor.
    #[inline]
    pub fn run_jobs(&mut self) -> JsResult<()> {
        self.job_executor().run_jobs(self)
    }

    /// Abstract operation [`ClearKeptObjects`][clear].
    ///
    /// Clears all objects maintained alive by calls to the [`AddToKeptObjects`][add] abstract
    /// operation, used within the [`WeakRef`][weak] constructor.
    ///
    /// [clear]: https://tc39.es/ecma262/multipage/executable-code-and-execution-contexts.html#sec-clear-kept-objects
    /// [add]: https://tc39.es/ecma262/multipage/executable-code-and-execution-contexts.html#sec-addtokeptobjects
    /// [weak]: https://tc39.es/ecma262/multipage/managing-memory.html#sec-weak-ref-objects
    #[inline]
    pub fn clear_kept_objects(&mut self) {


#### Step 9
Action:
- tool: bash
  command: cd /app && sed -i '504a\
\
    \/\/\/ Creates a new root [`EvaluationHandle`].\
    \/\/\/\
    \/\/\/ The handle can be used to cancel evaluation, module loading, and job execution.\
    #[must_use]\
    pub fn new_evaluation_handle(\&self) -> EvaluationHandle {\
        EvaluationHandle::new()\
    }\
\
    \/\/\/ Creates a new child [`EvaluationHandle`] from an existing handle.\
    \/\/\/\
    \/\/\/ The child handle shares the cancellation lineage: cancelling the parent\
    \/\/\/ will also cancel the child, but cancelling the child does not cancel the parent.\
    #[must_use]\
    pub fn new_child_evaluation_handle(\&self, handle: \&EvaluationHandle) -> EvaluationHandle {\
        handle.child()\
    }\
\
    \/\/\/ Evaluates the given source under an [`EvaluationHandle`].\
    \/\/\/\
    \/\/\/ If the handle is already cancelled, this fails before any user code runs.\
    pub fn eval_with_evaluation<R: boa_parser::source::ReadChar>(\
        \&mut self,\
        src: boa_parse...<truncated>
Observation:
returncode: 0

### Minimal Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: cd /app && sed -i 's/use crate::job::Job;/use crate::evaluation_handle::EvaluationHandle;\nuse crate::job::Job;/' core/engine/src/context/mod.rs
Observation:
returncode: 0

#### Step 2
Action:
- tool: bash
  command: cd /app && sed -i 's/    data: HostDefined,/    data: HostDefined,\n\n    \/\/\/ The currently active evaluation handle, if any.\n    current_eval_handle: Option<EvaluationHandle>,/' core/engine/src/context/mod.rs
Observation:
returncode: 0

#### Step 3
Action:
- tool: bash
  command: cd /app && sed -i 's/            data: HostDefined::default(),/            data: HostDefined::default(),\n            current_eval_handle: None,/' core/engine/src/context/mod.rs
Observation:
returncode: 0

#### Step 4
Action:
- tool: bash
  command: cd /app && sed -i '504a\
\
    \/\/\/ Creates a new root [`EvaluationHandle`].\
    \/\/\/\
    \/\/\/ The handle can be used to cancel evaluation, module loading, and job execution.\
    #[must_use]\
    pub fn new_evaluation_handle(\&self) -> EvaluationHandle {\
        EvaluationHandle::new()\
    }\
\
    \/\/\/ Creates a new child [`EvaluationHandle`] from an existing handle.\
    \/\/\/\
    \/\/\/ The child handle shares the cancellation lineage: cancelling the parent\
    \/\/\/ will also cancel the child, but cancelling the child does not cancel the parent.\
    #[must_use]\
    pub fn new_child_evaluation_handle(\&self, handle: \&EvaluationHandle) -> EvaluationHandle {\
        handle.child()\
    }\
\
    \/\/\/ Evaluates the given source under an [`EvaluationHandle`].\
    \/\/\/\
    \/\/\/ If the handle is already cancelled, this fails before any user code runs.\
    pub fn eval_with_evaluation<R: boa_parser::source::ReadChar>(\
        \&mut self,\
        src: boa_parse...<truncated>
Observation:
returncode: 0

# Chunk 5

## Graph Contrastive
Phase: read (action steps 40-44 of this trajectory, 5 steps total)
Anchor: step 4 (last successful write in this phase)
Minimal subgraph: [0, 4] (anchor + dependency closure, failed non-explore steps filtered out)
Removable steps (not in closure of anchor 4): [1, 2, 3, 5]. A batching script that skips or merges these would not affect the anchor's outcome.

### Predecessor Context (from before this phase)

#### Earlier step (global 19)

#### Step 1
Action:
- tool: bash
  command: grep -n "fn run\|fn run_async" ./core/engine/src/vm/mod.rs | head -20
Observation:
returncode: 0
output:
867:    pub(crate) async fn run_async_with_budget(&mut self, budget: u32) -> CompletionRecord {
902:    pub(crate) fn run(&mut self) -> CompletionRecord {


#### Earlier step (global 23)

#### Step 1
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0

#### Earlier step (global 24)

#### Step 1
Action:
- tool: bash
  command: sed -n '521,620p' /app/core/engine/src/job.rs
Observation:
returncode: 0
output:
pub enum Job {
    /// A `Promise`-related job.
    ///
    /// See [`PromiseJob`] for more information.
    PromiseJob(PromiseJob),
    /// A [`Future`]-related job.
    ///
    /// See [`NativeAsyncJob`] for more information.
    AsyncJob(NativeAsyncJob),
    /// A generic job that is to be executed after a number of milliseconds.
    ///
    /// See [`TimeoutJob`] for more information.
    TimeoutJob(TimeoutJob),
    /// A generic job.
    ///
    /// See [`GenericJob`] for more information.
    GenericJob(GenericJob),
}

impl From<NativeAsyncJob> for Job {
    fn from(native_async_job: NativeAsyncJob) -> Self {
        Job::AsyncJob(native_async_job)
    }
}

impl From<PromiseJob> for Job {
    fn from(promise_job: PromiseJob) -> Self {
        Job::PromiseJob(promise_job)
    }
}

impl From<TimeoutJob> for Job {
    fn from(job: TimeoutJob) -> Self {
        Job::TimeoutJob(job)
    }
}

impl From<GenericJob> for Job {
    fn from(job: GenericJob) -> Self {
 ...<truncated>

#### Earlier step (global 25)

#### Step 1
Action:
- tool: bash
  command: rm /app/core/engine/src/evaluation_handle.rs
Observation:
returncode: 0

#### Earlier step (global 26)

#### Step 1
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0
### Original Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: grep -n "enqueue_job" /app/core/engine/src/builtins/promise/mod.rs | head -20
Observation:
returncode: 0
output:
2248:                    .enqueue_job(fulfill_job.into(), context);
2270:                    .enqueue_job(reject_job.into(), context);
2343:                context.job_executor().enqueue_job(job.into(), context);
2532:                    context.job_executor().enqueue_job(job.into(), context);


#### Step 2
Action:
- tool: bash
  command: grep -rn "\.enqueue_job(" /app/core/engine/src/ | head -30
Observation:
returncode: 0
output:
/app/core/engine/src/context/mod.rs:497:        self.job_executor().enqueue_job(job, self);
/app/core/engine/src/context/mod.rs:560:        self.enqueue_job(job);
/app/core/engine/src/object/builtins/jspromise.rs:306:        context.enqueue_job(
/app/core/engine/src/builtins/promise/mod.rs:2248:                    .enqueue_job(fulfill_job.into(), context);
/app/core/engine/src/builtins/promise/mod.rs:2270:                    .enqueue_job(reject_job.into(), context);
/app/core/engine/src/builtins/promise/mod.rs:2343:                context.job_executor().enqueue_job(job.into(), context);
/app/core/engine/src/builtins/promise/mod.rs:2532:                    context.job_executor().enqueue_job(job.into(), context);
/app/core/engine/src/builtins/atomics/futex.rs:594:        context.enqueue_job(job.into());
/app/core/engine/src/builtins/atomics/futex.rs:602:    context.enqueue_job(
/app/core/engine/src/module/source.rs:585:                    context.enqueue_job(async_j...<truncated>

#### Step 3
Action:
- tool: bash
  command: grep -B5 -A10 "#\[derive\|impl.*for Job" /app/core/engine/src/job.rs | head -30
Observation:
returncode: 0
output:
        }
    }
}

/// Flag that can only be set once.
#[derive(Debug, Clone)]
pub(crate) struct OnceFlag(Rc<Cell<bool>>);

impl OnceFlag {
    /// Creates a new `OnceFlag`.
    pub(crate) fn new() -> Self {
        Self(Rc::new(Cell::new(false)))
    }

    /// Sets this `OnceFlag` to `true`.
    pub(crate) fn set(&self) {
--
/// An ECMAScript [Job] that runs after a certain amount of time.
///
/// This represents the [HostEnqueueTimeoutJob] operation from the specification.
///
/// [HostEnqueueTimeoutJob]: https://tc39.es/ecma262/#sec-hostenqueuetimeoutjob
#[derive(Debug)]
pub struct TimeoutJob {
    /// The distance in milliseconds in the future when the job should run.
    /// This will be added to the current time when the job is enqueued.
    timeout: JsDuration,
    /// The job to run after the time has passed.
    job: NativeJob,
    /// Signals if the timeout job was cancelled.


#### Step 4
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0

#### Step 5
Action:
- tool: bash
  command: grep -B5 -A15 "^pub enum Job" /app/core/engine/src/job.rs
Observation:
returncode: 0
output:
///
/// Additionally, each job type can have additional requirements that must also be followed in addition
/// to the previous ones.
#[non_exhaustive]
#[derive(Debug)]
pub enum Job {
    /// A `Promise`-related job.
    ///
    /// See [`PromiseJob`] for more information.
    PromiseJob(PromiseJob),
    /// A [`Future`]-related job.
    ///
    /// See [`NativeAsyncJob`] for more information.
    AsyncJob(NativeAsyncJob),
    /// A generic job that is to be executed after a number of milliseconds.
    ///
    /// See [`TimeoutJob`] for more information.
    TimeoutJob(TimeoutJob),
    /// A generic job.
    ///
    /// See [`GenericJob`] for more information.


### Minimal Chunk Trajectory

#### Step 1
Action:
- tool: bash
  command: cat > /app/core/engine/src/evaluation_handle.rs << 'EVALEOF'
//! Evaluation handles for cancelling evaluations across nested contexts.
//!
//! [`EvaluationHandle`] allows cancelling script/module evaluation and job execution
//! from outside the running engine. Handles form a parent-child hierarchy where
//! cancelling a parent cascades to all descendants, but cancelling a child does
//! not affect its parent.
//!
//! Cancellation is first-wins: the first effective cancellation determines its
//! reason and later attempts cannot replace it.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::{Context, JsNativeError, JsResult, JsValue};

/// Internal state shared between an [`EvaluationHandle`] and its clones/children.
#[derive(Debug)]
struct EvaluationHandleState {
    /// Whether this specific handle node has been directly cancelled.
    cancelled: Cell<bool>,
    /// The cancellation reason, if one was explicitly set.
    reason: RefCell<Option<JsValue>>,
    /// The parent...<truncated>
Observation:
returncode: 0

# Your task
Modify, add, merge, or remove scripts under your cwd based on the samples above. After each change, run the verification steps listed above. Do NOT edit the prompt file or sample files. Finish once scripts + intro.json + instruction.md are saved and verified.