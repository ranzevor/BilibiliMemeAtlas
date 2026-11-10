#!/bin/sh
set -eu

git_dir="$(git rev-parse --path-format=absolute --git-common-dir)"
active_hooks="$(git rev-parse --path-format=absolute --git-path hooks)"
staged_hooks="$(mktemp -d "$git_dir/.memeatlas-hooks.XXXXXX")"
keep_hooks=false
config_scope=""
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  effective_hooks="$(git rev-parse --path-format=absolute --git-path hooks 2>/dev/null)" || effective_hooks=""
  configured_hooks=""
  if [ -n "$config_scope" ]; then
    configured_hooks="$(git config "$config_scope" --get core.hooksPath 2>/dev/null)" || configured_hooks=""
  fi
  if [ "$keep_hooks" = true ] || [ "$configured_hooks" = "$staged_hooks" ] || { [ -d "$effective_hooks" ] &&
    [ "$(CDPATH= cd -- "$effective_hooks" && pwd -P)" = "$(CDPATH= cd -- "$staged_hooks" && pwd -P)" ]; }; then
    :
  else
    rm -rf -- "$staged_hooks"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

MSYS_NO_PATHCONV=1 git show refs/remotes/origin/main:.github/scripts/governance_gate.cjs > "$staged_hooks/governance_gate.cjs"
if [ ! -s "$staged_hooks/governance_gate.cjs" ]; then
  printf '%s\n' '可信主分支中的提交校验器为空，已中止安装。' >&2
  exit 1
fi
node --check "$staged_hooks/governance_gate.cjs"

cat > "$staged_hooks/commit-msg" <<'HOOK'
#!/bin/sh
set -eu
hooks_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec node "$hooks_dir/governance_gate.cjs" message-file "$1"
HOOK
chmod +x "$staged_hooks/commit-msg"

# 不静默停用或替换现有钩子；只允许更新由本安装器生成的 commit-msg。
for existing in "$active_hooks"/*; do
  [ -f "$existing" ] || continue
  case "$existing" in
    *.sample) continue ;;
    */commit-msg)
      case "$active_hooks" in
        "$git_dir"/.memeatlas-hooks.*)
          if cmp -s "$existing" "$staged_hooks/commit-msg"; then continue; fi
          ;;
      esac
      ;;
    *)
      [ -x "$existing" ] || continue
      ;;
  esac
  printf '已有生效钩子，未覆盖：%s\n' "$existing" >&2
  exit 1
done

config_scope=--local
if git config --worktree --get core.hooksPath >/dev/null 2>&1; then
  config_scope=--worktree
fi
previous_hooks=""
had_previous=false
if previous_hooks="$(git config "$config_scope" --get core.hooksPath)"; then
  had_previous=true
fi

git config "$config_scope" core.hooksPath "$staged_hooks"
effective_hooks="$(git rev-parse --path-format=absolute --git-path hooks)"
if ! { [ -d "$effective_hooks" ] &&
  [ "$(CDPATH= cd -- "$effective_hooks" && pwd -P)" = "$(CDPATH= cd -- "$staged_hooks" && pwd -P)" ]; }; then
  keep_hooks=true
  if [ "$had_previous" = true ]; then
    git config "$config_scope" core.hooksPath "$previous_hooks"
  else
    git config "$config_scope" --unset core.hooksPath
  fi
  keep_hooks=false
  printf '%s\n' '钩子路径未生效，已恢复原有配置。' >&2
  exit 1
fi
printf '%s\n' '本地提交检查已安装到 Git 私有目录。'
