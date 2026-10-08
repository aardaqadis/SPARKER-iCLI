# Put SPARKER iCLI on GitHub

## The existing project repository

This project is already connected to
[aardaqadis/SPARKER-iCLI](https://github.com/aardaqadis/SPARKER-iCLI).
The local repository is the `Termatelier` folder inside the original workspace.
The preparation adds documentation and CI and removes generated diagnostics,
editor settings and the personal painting from tracking while keeping local files.

Open PowerShell in that repository folder, then:

```powershell
git status
git log -1 --oneline
git push origin main
```

Refresh the GitHub page and open **Actions** to see the test/build workflow run.
The repository is currently public. Files removed by this cleanup remain in the
earlier commit's history; this preparation does not rewrite published history.

## Create a new repository

The source ZIP contains a complete flat Python project, documentation and
launchers. Virtual environments, scratch work, diagnostics, builds and the
personal painting are excluded. For an existing local clone, use its Git
repository; for a fresh ZIP, initialize Git as described at the end.

1. Sign in to GitHub and open [New repository](https://github.com/new).
2. Name it **sparker-icli**. A useful description is:
   **Minimal terminal painting editor with mouse tools, layers and a full-screen CLI.**
3. Choose **Public** for a project anyone can view, or **Private** while developing.
4. Leave the README, .gitignore and license initialization options empty.
   Those files are already committed locally.
5. Click **Create repository**, then copy its **HTTPS** URL.

These steps follow [GitHub's repository creation guide](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository).

Open PowerShell in your extracted source or existing repository folder. Replace `YOUR_USERNAME`
with your GitHub account name, or paste the exact URL you copied:

```powershell
git status
git remote add origin https://github.com/YOUR_USERNAME/sparker-icli.git
git push -u origin main
```

`origin` names the GitHub copy. The push uploads the first commit and connects
your local `main` branch to it. Git may open a browser for authentication.
If you prefer the installed GitHub CLI, authenticate first with:

```powershell
gh auth login
gh auth setup-git
```

Choose **GitHub.com**, **HTTPS** and browser login. Then run the push above.
See [GitHub's existing-project instructions](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github?platform=windows).

Refresh the repository page. Your README should display the logo and editor
preview. Open **Actions** to see the first automatic test/build run. The workflow
is configured locally; it has not run on GitHub before publication.

## Alternative: create it entirely with GitHub CLI

Use this instead of the website route, from the prepared repository folder:

```powershell
gh auth login
gh auth setup-git
gh repo create sparker-icli --private --source . --remote origin --push
```

Use `--public` instead of `--private` if you want public visibility.
This command creates the remote repository and pushes your committed source.
See the [official gh repo create reference](https://cli.github.com/manual/gh_repo_create).

## Upload later changes

From the same local repository:

```powershell
git status
git add README.md USAGE.md src tests tools pyproject.toml
git commit -m "Describe the change"
git push
```

Add any changed root documentation or `.github` files by name as needed.
The existing ignore rules keep local work, installations and diagnostics out
of normal Git staging. The **Changes** tab in an editor or `git diff --staged`
shows exactly what the next commit includes.

If `origin` was already added, check it with `git remote -v`; update it with
`git remote set-url origin YOUR_REPOSITORY_URL`. If authentication fails, use
`gh auth login` and `gh auth setup-git`, then retry `git push`.

## Downloads and releases

GitHub's **Code → Download ZIP** provides the tracked source and launchers.
The workflow also creates downloadable wheel/source-package artifacts on
successful runs. A tagged GitHub Release can attach a wheel and the prepared
application ZIP when you choose to publish a version.

Keep generated wheel files in a Release or Actions artifact; `dist/` is ignored
in the source repository. The README and package metadata retain the project's
existing MIT license.

## Starting from the source ZIP

The prepared source ZIP omits Git's internal `.git` directory. Extract it and
open its top-level folder, then initialize and commit before following the
website or CLI publishing steps:

```powershell
git init -b main
git add .
git commit -m "Prepare SPARKER iCLI for GitHub"
```

If Git asks for your identity, configure your chosen name and GitHub commit
email locally with `git config user.name "Your Name"` and
`git config user.email "Your GitHub commit email"`, then retry the commit.
