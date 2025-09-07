name: "NFL Fetch Scan Alert"

on:
  workflow_dispatch:
    inputs:
      fetch:
        description: "Fetch projections via Shiny first? (true/false)"
        required: true
        default: "true"
      threshold_ev:
        description: "EV threshold like 0.06"
        required: false
        default: "0.06"
      profile:
        description: "Markets profile (base/heavy)"
        required: false
        default: "base"

jobs:
  fetch_projections:
    # Always define the job so 'needs:' works; guard steps instead.
    runs-on: ubuntu-latest
    outputs:
      did_fetch: ${{ steps.mark.outputs.did_fetch }}
    steps:
      - name: Checkout
        if: ${{ inputs.fetch == 'true' }}
        uses: actions/checkout@v4

      - name: Setup R
        if: ${{ inputs.fetch == 'true' }}
        uses: r-lib/actions/setup-r@v2
        with:
          use-public-rspm: true

      - name: Setup Chrome
        if: ${{ inputs.fetch == 'true' }}
        id: chrome
        uses: browser-actions/setup-chrome@v1

      - name: System libs for R 'curl' and headless Chrome
        if: ${{ inputs.fetch == 'true' }}
        run: |
          sudo apt-get update
          sudo apt-get install -y libcurl4-openssl-dev libnss3 libasound2t64

      - name: Install minimal R packages
        if: ${{ inputs.fetch == 'true' }}
        run: |
          Rscript -e 'install.packages(c("chromote","fs","jsonlite"), repos="https://cloud.r-project.org")'

      - name: Create artifacts dir
        if: ${{ inputs.fetch == 'true' }}
        run: mkdir -p artifacts

      - name: Download projections CSV from Shiny
        if: ${{ inputs.fetch == 'true' }}
        env:
          FFA_SHINY_URL: https://ffashiny.shinyapps.io/newApp/
          # Prefer your robust R script's fallback; selector can be overridden in script if needed
          FFA_DOWNLOAD_SELECTOR: 'a[data-export="projections"]'
          FFA_DOWNLOAD_TIMEOUT: "150"
          FFA_OUT: artifacts/projections.csv
          CHROMOTE_CHROME: ${{ steps.chrome.outputs.chrome-path }}
        run: Rscript scripts/ffa_shiny_download.R

      - name: Upload projections artifact
        if: ${{ inputs.fetch == 'true' }}
        uses: actions/upload-artifact@v4
        with:
          name: projections
          path: artifacts/projections.csv
          if-no-files-found: error

      - name: Mark fetched
        id: mark
        run: |
          if [ "${{ inputs.fetch }}" = "true" ]; then
            echo "did_fetch=true" >> "$GITHUB_OUTPUT"
          else
            echo "did_fetch=false" >> "$GITHUB_OUTPUT"
          fi

  scan_and_alert:
    needs: fetch_projections
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Create dirs
        run: mkdir -p artifacts data

      - name: Download projections artifact (if fetched & available)
        if: ${{ inputs.fetch == 'true' && needs.fetch_projections.outputs.did_fetch == 'true' }}
        uses: actions/download-artifact@v4
        with:
          name: projections
          path: data

      - name: Choose projections file
        run: |
          set -e
          if [ -f data/projections.csv ]; then
            echo "Using fetched data/projections.csv"
          elif [ -f data/raw_stats_current.csv ]; then
            echo "Using committed data/raw_stats_current.csv"
            cp data/raw_stats_current.csv data/projections.csv
          elif [ -f data/projections.csv ]; then
            echo "Using committed data/projections.csv"
          else
            echo "No projections file found. Add data/projections.csv (or raw_stats_current.csv) or run with fetch=true."
            exit 1
          fi
          echo "Top of projections:"
          head -n 3 data/projections.csv || true

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Python deps
        run: |
          python -m pip install -U pip
          python -m pip install pandas numpy requests

      - name: Run scan + alert
        env:
          ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}
          SLACK_WEBHOOK: ${{ secrets.SLACK_WEBHOOK }}
          EDGE_THRESHOLD: ${{ inputs.threshold_ev }}
          MARKETS_PROFILE: ${{ inputs.profile }}
          PROJECTIONS_PATH: data/projections.csv
        run: python agent_cli.py

      - name: Show advice in logs (for quick mobile view)
        run: |
          if [ -f artifacts/advice.txt ]; then
            echo "----- ADVICE FEED -----"
            cat artifacts/advice.txt
            echo "-----------------------"
          else
            echo "No advice.txt generated."
          fi

      - name: Upload edges artifact(s)
        uses: actions/upload-artifact@v4
        with:
          name: edges
          path: |
            artifacts/edges.csv
            artifacts/advice.txt
          if-no-files-found: warn
