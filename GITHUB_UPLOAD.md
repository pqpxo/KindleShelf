<!-- version 1 -->
# Uploading KindleShelf Local through the GitHub website

This package is prepared for a new GitHub repository. It intentionally does not contain your `.env`, ebooks, generated conversion cache, passwords, or secret keys.

## Upload using the GitHub web interface

1. Create a new empty repository on GitHub.
2. Do not add a README, `.gitignore`, or licence when creating it, because this package already includes them.
3. Extract `kindleshelf-local-github-v1.2.2.zip` on your computer.
4. Open the extracted folder.
5. Select every file and folder, including the dotfiles and `.github` folder.
6. Drag the selected items onto the repository's **Add file → Upload files** page.
7. Enter a commit message such as `Initial KindleShelf Local v1.2.2 release`.
8. Select **Commit changes**.

## Important

- Do not upload your real `.env` file.
- Do not upload ebooks from `library/`.
- Do not upload runtime files from `data/`.
- Create `.env` on the Docker host by copying `.env.example` after cloning or downloading the repository.
