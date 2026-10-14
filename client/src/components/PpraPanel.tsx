"use client";

import { useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import PpraResults from "@/components/PpraResults";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import { isRunActive, startPpraScrape, pauseRun, resumeRun } from "@/lib/api";

export default function PpraPanel() {
  const [keyword, setKeyword] = useState("");
  const [tenderNumber, setTenderNumber] = useState("");
  const [closingDate, setClosingDate] = useState("");
  const [tenderType, setTenderType] = useState("ALL");
  const [category, setCategory] = useState("ALL");
  const [sector, setSector] = useState("ALL");
  const [nature, setNature] = useState("ALL");
  const [organization, setOrganization] = useState("");
  const [country, setCountry] = useState("Pakistan");
  const [advertiseFrom, setAdvertiseFrom] = useState("");
  const [advertiseTo, setAdvertiseTo] = useState("");
  const [status, setStatus] = useState("ALL");
  const [city, setCity] = useState("");

  const [applyClosingFilter, setApplyClosingFilter] = useState(true);
  const [pausing, setPausing] = useState(false);

  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("ppra");

  const isRunning = isRunActive(run);
  const isPaused = run?.status === "paused";

  const handleStart = (livePreview = false) =>
    launch(() =>
      startPpraScrape(
        {
          keyword: keyword.trim() || null,
          tender_number: tenderNumber.trim() || null,
          closing_date: closingDate.trim() || null,
          tender_type: tenderType !== "ALL" ? tenderType : null,
          category: category !== "ALL" ? category : null,
          sector: sector !== "ALL" ? sector : null,
          nature: nature !== "ALL" ? nature : null,
          organization: organization.trim() || null,
          country: country.trim() || null,
          advertise_from: advertiseFrom.trim() || null,
          advertise_to: advertiseTo.trim() || null,
          status: status !== "ALL" ? status : null,
          city: city.trim() || null,
        },
        {
          apply_closing_filter: applyClosingFilter,
        },
        livePreview
      )
    );

  const handleTogglePause = async () => {
    if (!run) return;
    setPausing(true);
    try {
      if (isPaused) {
        await resumeRun(run.run_id);
      } else {
        await pauseRun(run.run_id);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to toggle pause");
    } finally {
      setPausing(false);
    }
  };

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="PPRA Search & Filter Criteria"
        description="Filter active federal tenders published by the Public Procurement Regulatory Authority (epms.ppra.gov.pk)."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Field
            label="Keyword Search"
            value={keyword}
            onChange={setKeyword}
            disabled={isRunning}
            placeholder="e.g. transformer, software"
          />
          <Field
            label="Tender Number"
            value={tenderNumber}
            onChange={setTenderNumber}
            disabled={isRunning}
            placeholder="e.g. TS0000015884E"
          />
          <Field
            label="Closing Date"
            type="date"
            value={closingDate}
            onChange={setClosingDate}
            disabled={isRunning}
            placeholder="YYYY-MM-DD"
          />

          <div>
            <label className="block text-xs font-medium text-ink-700">Tender Type</label>
            <select
              value={tenderType}
              onChange={(e) => setTenderType(e.target.value)}
              disabled={isRunning}
              className="mt-1 block w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs text-ink-800 focus:border-ink-400 focus:outline-none"
            >
              <option value="ALL">All Types</option>
              <option value="Tender Notice">Tender Notice</option>
              <option value="Pre-qualification Notice">Pre-qualification (PQ)</option>
              <option value="Request for Proposal">Request for Proposal (RFP)</option>
              <option value="Expression of Interest">Expression of Interest (EOI)</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-ink-700">Category</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              disabled={isRunning}
              className="mt-1 block w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs text-ink-800 focus:border-ink-400 focus:outline-none"
            >
              <option value="ALL">All Categories</option>
              <option value="Goods">Goods</option>
              <option value="Works">Works</option>
              <option value="Consultancy Services">Consultancy Services</option>
              <option value="Non-consultancy Services">Non-consultancy Services</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-ink-700">Sector</label>
            <select
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              disabled={isRunning}
              className="mt-1 block w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs text-ink-800 focus:border-ink-400 focus:outline-none"
            >
              <option value="ALL">All Sectors</option>
              <option value="Electrical Items">Electrical Items</option>
              <option value="Information Technology">Information Technology</option>
              <option value="Medical Equipment">Medical Equipment</option>
              <option value="Construction & Works">Construction &amp; Works</option>
              <option value="Ammunition">Ammunition</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-ink-700">Nature</label>
            <select
              value={nature}
              onChange={(e) => setNature(e.target.value)}
              disabled={isRunning}
              className="mt-1 block w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs text-ink-800 focus:border-ink-400 focus:outline-none"
            >
              <option value="ALL">All</option>
              <option value="Local">Local</option>
              <option value="International">International</option>
            </select>
          </div>

          <Field
            label="Organization / Ministry"
            value={organization}
            onChange={setOrganization}
            disabled={isRunning}
            placeholder="e.g. WAPDA, SNGPL, NHA"
          />

          <Field
            label="Country"
            value={country}
            onChange={setCountry}
            disabled={isRunning}
            placeholder="Pakistan"
          />

          <Field
            label="Advertise Date From"
            type="date"
            value={advertiseFrom}
            onChange={setAdvertiseFrom}
            disabled={isRunning}
            placeholder="YYYY-MM-DD"
          />

          <Field
            label="Advertise Date To"
            type="date"
            value={advertiseTo}
            onChange={setAdvertiseTo}
            disabled={isRunning}
            placeholder="YYYY-MM-DD"
          />

          <div>
            <label className="block text-xs font-medium text-ink-700">Status</label>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              disabled={isRunning}
              className="mt-1 block w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs text-ink-800 focus:border-ink-400 focus:outline-none"
            >
              <option value="ALL">All Statuses</option>
              <option value="Published">Published</option>
              <option value="Published Corrigendum">Published Corrigendum</option>
              <option value="Cancelled">Cancelled</option>
            </select>
          </div>

          <Field
            label="City"
            value={city}
            onChange={setCity}
            disabled={isRunning}
            placeholder="e.g. Islamabad, Lahore, Karachi"
          />
        </div>

        <div className="mt-4 border-t border-ink-100 pt-4">
          <label className="inline-flex items-center gap-2 cursor-pointer text-xs font-medium text-ink-700">
            <input
              type="checkbox"
              checked={applyClosingFilter}
              onChange={(e) => setApplyClosingFilter(e.target.checked)}
              disabled={isRunning}
              className="h-4 w-4 rounded border-ink-300 text-emerald-600 focus:ring-emerald-500"
            />
            Apply 7-day runway closing filter (drop tenders closing in &lt; 7 days)
          </label>
        </div>
      </Card>

      <LaunchBar summary="PPRA active tenders scraper ready with cooperative pause/resume and live tracking.">
        <div className="flex items-center gap-2">
          {isRunning && (
            <button
              type="button"
              onClick={handleTogglePause}
              disabled={pausing}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800 shadow-sm transition hover:bg-amber-100"
            >
              {isPaused ? "▶ Resume" : "⏸ Pause"}
            </button>
          )}
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="ppra" />
          <ScraperControls
            run={run}
            onError={setError}
            onStatusChange={applyStatus}
          />
          <RefreshButton run={run} hasSession={hasSession} onReset={reset} />
          <StartButton
            onClick={() => handleStart()}
            disabled={starting || isRunning}
            running={isRunning}
            starting={starting}
          >
            Start PPRA Scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      <SessionActivity portal="ppra" />
      {run && <PpraResults runId={run.run_id} initialBids={run.bids} />}
    </div>
  );
}

