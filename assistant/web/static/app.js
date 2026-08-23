(() => {
  "use strict";

  function dbgLog(event, data = {}) {
    try {
      fetch("http://127.0.0.1:7571/ingest/e1ea32ce-c1b3-4a1c-b4f8-c821ab1d50ab", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Debug-Session-Id": "469bfe",
        },
        body: JSON.stringify({
          sessionId: "469bfe",
          location: "assistant/web/static/app.js",
          event,
          data,
          timestamp: Date.now(),
        }),
      }).catch(() => {});
    } catch (_error) {
      /* instrumentation must never break the UI */
    }
  }

  const terminalStatuses = new Set([
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
  ]);

  const jobTypeLabels = {
    daily_refresh: "今日更新",
    environment_check: "店铺连接检查",
    shipment_sync: "物流同步",
    followup_generate: "跟进待办生成",
    report_export: "报表导出",
  };

  const statusLabels = {
    pending: "等待执行",
    running: "正在执行",
    succeeded: "已完成",
    failed: "执行失败",
    cancelled: "已取消",
    interrupted: "已中断",
  };

  const statusTones = {
    pending: "warning",
    running: "info",
    succeeded: "success",
    failed: "error",
    cancelled: "neutral",
    interrupted: "neutral",
  };

  const streamEventTypes = [
    "job.started",
    "job.progress",
    "job.stage",
    "job.warning",
    "job.failed",
    "job.completed",
  ];

  const activeMonitors = new Map();

  function getJobTypeLabel(jobType) {
    return jobTypeLabels[jobType] || jobType || "后台任务";
  }

  function getStatusLabel(status) {
    return statusLabels[status] || status || "未知状态";
  }

  function getStatusTone(status) {
    return statusTones[status] || "neutral";
  }

  function formatNumber(value) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue)
      ? new Intl.NumberFormat("zh-CN").format(numericValue)
      : String(value ?? "0");
  }

  function formatDateTime(value) {
    if (!value) {
      return "";
    }
    const dateValue = new Date(value);
    if (Number.isNaN(dateValue.getTime())) {
      return String(value);
    }
    return new Intl.DateTimeFormat("zh-CN", {
      dateStyle: "short",
      timeStyle: "medium",
    }).format(dateValue);
  }

  function parseResultSummary(job) {
    if (!job || !job.result_summary) {
      return null;
    }
    try {
      return JSON.parse(job.result_summary);
    } catch (_error) {
      return {summary: String(job.result_summary)};
    }
  }

  function createElement(elementName, className, textContent) {
    const element = document.createElement(elementName);
    if (className) {
      element.className = className;
    }
    if (textContent !== undefined) {
      element.textContent = textContent;
    }
    return element;
  }

  function createStatusToken(status) {
    const tone = getStatusTone(status);
    const token = createElement("span", `token token--${tone}`);
    token.append(
      createElement("span", `status-dot status-dot--${tone}`),
      document.createTextNode(getStatusLabel(status)),
    );
    return token;
  }

  function appendMetadataRow(metadataList, label, value) {
    metadataList.append(
      createElement("dt", null, label),
      createElement("dd", null, value),
    );
  }

  function renderJobSnapshot(target, job) {
    if (!target || !job) {
      return;
    }

    const metadataList = createElement("dl", "metadata");
    metadataList.append(
      createElement("dt", null, "任务"),
      createElement("dd", null, getJobTypeLabel(job.job_type)),
      createElement("dt", null, "状态"),
    );
    const statusValue = createElement("dd");
    statusValue.append(createStatusToken(job.status));
    metadataList.append(statusValue);
    appendMetadataRow(
      metadataList,
      "进度",
      job.progress_total > 0
        ? `${formatNumber(job.progress_current)} / ${formatNumber(job.progress_total)}`
        : "等待任务开始",
    );
    appendMetadataRow(metadataList, "当前步骤", job.progress_message || "等待任务开始");
    if (job.finished_at) {
      appendMetadataRow(metadataList, "完成时间", formatDateTime(job.finished_at));
    }

    target.replaceChildren(metadataList);
  }

  function getResultEntries(job, result) {
    if (!result || typeof result !== "object") {
      return [];
    }

    if (job.job_type === "daily_refresh") {
      const shipment = result.shipment || {};
      const followup = result.followup || {};
      return [
        ["物流记录", `${formatNumber(shipment.synchronized)} 条已读取`],
        ["物流变化", `${formatNumber(shipment.changed)} 条已更新`],
        ["处理中样品", `${formatNumber(shipment.processing)} 条`],
        ["新增跟进待办", `${formatNumber(followup.created)} 条`],
      ];
    }

    if (job.job_type === "shipment_sync") {
      return [
        ["物流记录", `${formatNumber(result.synchronized)} 条已读取`],
        ["物流变化", `${formatNumber(result.changed)} 条已更新`],
        ["处理中样品", `${formatNumber(result.processing)} 条`],
      ];
    }

    if (job.job_type === "followup_generate") {
      return [["新增跟进待办", `${formatNumber(result.created)} 条`]];
    }

    if (job.job_type === "environment_check") {
      const stores = result.stores || {};
      return [
        ["运行店铺", `${formatNumber(stores.count)} 家`],
        ["配置文件", result.config_exists ? "已配置" : "未配置"],
        ["店铺页面", result.probe === "ready" ? "可用" : "需要检查"],
      ];
    }

    if (job.job_type === "report_export") {
      return [["报表类型", result.kind || "已生成"]];
    }

    if (result.summary) {
      return [["结果", result.summary]];
    }

    return Object.entries(result)
      .filter(([, value]) => ["string", "number", "boolean"].includes(typeof value))
      .slice(0, 6)
      .map(([key, value]) => [key, String(value)]);
  }

  function renderJobResult(target, job) {
    if (!target || !job || !terminalStatuses.has(job.status)) {
      return;
    }

    target.replaceChildren();
    const heading = createElement(
      "h3",
      null,
      job.status === "succeeded" ? "运行结果" : getStatusLabel(job.status),
    );
    target.append(heading);

    if (job.status !== "succeeded") {
      target.append(
        createElement(
          "p",
          "task-error",
          job.error_summary || "任务没有完成，请查看执行过程后重试。",
        ),
      );
      if (job.error_code) {
        target.append(createElement("small", "text-secondary", `参考信息：${job.error_code}`));
      }
      return;
    }

    const result = parseResultSummary(job);
    const resultList = createElement("dl", "result-list");
    for (const [label, value] of getResultEntries(job, result)) {
      appendMetadataRow(resultList, label, value);
    }
    target.append(resultList);

    const actionList = createElement("p", "task-actions");
    const detailLink = createElement("a", "button-link", "查看任务详情");
    detailLink.href = `/jobs/${encodeURIComponent(job.id)}`;
    actionList.append(detailLink);

    if (job.job_type === "daily_refresh" || job.job_type === "shipment_sync") {
      const shipmentLink = createElement("a", "button-link button-link--secondary", "查看物流");
      shipmentLink.href = "/shipments";
      actionList.append(shipmentLink);
    }
    if (job.job_type === "daily_refresh" || job.job_type === "followup_generate") {
      const followupLink = createElement("a", "button-link button-link--secondary", "查看跟进待办");
      followupLink.href = "/followups";
      actionList.append(followupLink);
    }
    if (job.job_type === "report_export" && result && result.path) {
      const exportLink = createElement("a", "button-link button-link--secondary", "下载 CSV");
      const downloadUrl = new URL("/api/exports/download", window.location.origin);
      downloadUrl.searchParams.set("path", result.path);
      exportLink.href = downloadUrl.toString();
      actionList.append(exportLink);
    }
    target.append(actionList);
  }

  function appendEvent(target, eventPayload, monitor) {
    if (!target || !eventPayload || !eventPayload.sequence) {
      return;
    }
    const sequence = String(eventPayload.sequence);
    if (monitor.seenSequences.has(sequence)) {
      return;
    }
    monitor.seenSequences.add(sequence);

    const eventItem = createElement("li", `task-event task-event--${eventPayload.level || "info"}`);
    const eventTime = createElement("time", "task-event-time", formatDateTime(eventPayload.created_at));
    const eventMessage = createElement("span", "task-event-message", eventPayload.message || "状态已更新");
    eventItem.append(eventTime, eventMessage);
    target.append(eventItem);
    target.scrollTop = target.scrollHeight;
  }

  function getGlobalPanel() {
    return document.querySelector("[data-task-panel]");
  }

  function renderGlobalPanel(job, monitor) {
    const panel = getGlobalPanel();
    if (!panel || !monitor.useGlobalPanel) {
      return;
    }
    panel.hidden = false;
    const title = panel.querySelector("[data-task-title]");
    const status = panel.querySelector("[data-task-status]");
    const progress = panel.querySelector("[data-task-progress]");
    const progressText = panel.querySelector("[data-task-progress-text]");
    const progressMessage = panel.querySelector("[data-task-progress-message]");
    const result = panel.querySelector("[data-task-result]");
    const cancelForm = panel.querySelector("form[data-job-cancel]");
    const cancelButton = cancelForm?.querySelector("button");
    panel.classList.toggle("task-panel--warning", monitor.hasWarning);

    if (title) {
      title.textContent = getJobTypeLabel(job.job_type);
    }
    if (status) {
      status.replaceChildren(createStatusToken(job.status));
    }
    if (cancelForm && cancelButton) {
      const cancellable = !terminalStatuses.has(job.status);
      if (cancellable) {
        cancelForm.action = `/api/jobs/${encodeURIComponent(job.id)}/cancel`;
        cancelForm.dataset.jobId = job.id;
      }
      const cancellationRequested = (job.progress_message || "").includes(
        "已请求取消",
      );
      cancelButton.hidden = !cancellable;
      cancelButton.disabled = cancellationRequested;
    }
    const hasProgress = Number(job.progress_total) > 0;
    if (progress) {
      progress.max = hasProgress ? Number(job.progress_total) : 1;
      progress.value = hasProgress ? Number(job.progress_current) : 0;
      progress.hidden = !hasProgress;
    }
    if (progressText) {
      progressText.textContent = hasProgress
        ? `${formatNumber(job.progress_current)} / ${formatNumber(job.progress_total)}`
        : "等待任务开始";
    }
    if (progressMessage) {
      progressMessage.textContent = job.progress_message || "正在排队，任务即将开始";
    }
    if (terminalStatuses.has(job.status)) {
      renderJobResult(result, job);
      panel.dataset.taskTerminal = "true";
    }
  }

  async function fetchJob(jobId) {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
      credentials: "same-origin",
      headers: {Accept: "application/json"},
    });
    if (!response.ok) {
      throw new Error(`任务状态读取失败（${response.status}）`);
    }
    return response.json();
  }

  function stopMonitor(monitor) {
    if (monitor.eventSource) {
      monitor.eventSource.close();
    }
    if (monitor.pollTimer) {
      window.clearInterval(monitor.pollTimer);
    }
    activeMonitors.delete(monitor.jobId);
  }

  function handleJobSnapshot(monitor, job) {
    monitor.latestJob = job;
    renderGlobalPanel(job, monitor);
    renderJobSnapshot(monitor.statusTarget, job);
    if (terminalStatuses.has(job.status)) {
      renderJobResult(monitor.resultTarget, job);
      stopMonitor(monitor);
      document.dispatchEvent(new CustomEvent("assistant:job-complete", {detail: job}));
    }
  }

  function showMonitorError(monitor, error) {
    const message = error instanceof Error ? error.message : String(error);
    const panel = getGlobalPanel();
    if (panel && monitor.useGlobalPanel) {
      const result = panel.querySelector("[data-task-result]");
      if (result) {
        result.replaceChildren(createElement("p", "task-error", message));
      }
    }
    if (monitor.resultTarget) {
      monitor.resultTarget.replaceChildren(createElement("p", "task-error", message));
    }
  }

  function resetGlobalPanel(monitor) {
    const panel = getGlobalPanel();
    if (!panel || !monitor.useGlobalPanel) {
      return;
    }
    panel.hidden = false;
    panel.classList.remove("task-panel--warning");
    panel.removeAttribute("data-task-terminal");
    panel.querySelector("[data-task-events]")?.replaceChildren();
    panel.querySelector("[data-task-result]")?.replaceChildren();
    const progressMessage = panel.querySelector("[data-task-progress-message]");
    if (progressMessage) {
      progressMessage.textContent = "任务即将开始";
    }
  }

  function startMonitor(jobId, options = {}) {
    if (!jobId) {
      return;
    }
    const existingMonitor = activeMonitors.get(jobId);
    if (existingMonitor) {
      return existingMonitor;
    }

    dbgLog("monitor-start", {jobId, useGlobalPanel: options.useGlobalPanel !== false});

    const monitor = {
      jobId,
      statusTarget: options.statusTarget || null,
      eventsTarget: options.eventsTarget || null,
      resultTarget: options.resultTarget || null,
      useGlobalPanel: options.useGlobalPanel !== false,
      eventSource: null,
      pollTimer: null,
      latestJob: null,
      seenSequences: new Set(),
      hasWarning: false,
    };
    activeMonitors.set(jobId, monitor);
    resetGlobalPanel(monitor);

    const eventTarget = monitor.eventsTarget || (monitor.useGlobalPanel
      ? getGlobalPanel()?.querySelector("[data-task-events]")
      : null);

    const refreshSnapshot = () => {
      fetchJob(jobId)
        .then((job) => {
          dbgLog("snapshot-ok", {jobId, status: job.status});
          handleJobSnapshot(monitor, job);
        })
        .catch((error) => {
          dbgLog("snapshot-error", {jobId, error: String(error)});
          showMonitorError(monitor, error);
        });
    };

    refreshSnapshot();
    monitor.pollTimer = window.setInterval(refreshSnapshot, 2000);
    monitor.eventSource = new EventSource(
      `/api/jobs/${encodeURIComponent(jobId)}/stream`,
      {withCredentials: true},
    );

    for (const eventType of streamEventTypes) {
      monitor.eventSource.addEventListener(eventType, (event) => {
        let eventPayload;
        try {
          eventPayload = JSON.parse(event.data);
        } catch (_error) {
          eventPayload = {message: event.data, sequence: event.lastEventId};
        }
        appendEvent(eventTarget, eventPayload, monitor);
        if (eventType === "job.warning") {
          monitor.hasWarning = true;
        }
        refreshSnapshot();
      });
    }

    monitor.eventSource.onerror = () => {
      if (!monitor.latestJob || !terminalStatuses.has(monitor.latestJob.status)) {
        const panel = getGlobalPanel();
        const progressMessage = panel?.querySelector("[data-task-progress-message]");
        if (progressMessage && monitor.useGlobalPanel) {
          progressMessage.textContent = "实时连接正在重试，任务仍会继续执行";
        }
      }
    };
    return monitor;
  }

  async function submitJobForm(form, event) {
    event.preventDefault();
    const submitButton = form.querySelector("button[type='submit'], button:not([type])");
    if (form.dataset.submitting === "true") {
      return;
    }
    form.dataset.submitting = "true";
    if (submitButton) {
      submitButton.disabled = true;
    }

    try {
      const response = await fetch(form.action, {
        method: (form.method || "POST").toUpperCase(),
        body: new FormData(form),
        credentials: "same-origin",
        headers: {Accept: "application/json"},
      });
      if (!response.ok) {
        throw new Error(`无法开始任务（${response.status}）`);
      }
      const payload = await response.json();
      dbgLog("job-submitted", {jobId: payload.job_id, deduplicated: payload.deduplicated});
      const monitor = startMonitor(payload.job_id, {
        useGlobalPanel: true,
      });
      const panel = getGlobalPanel();
      if (panel) {
        panel.hidden = false;
        panel.dataset.taskLabel = form.dataset.jobLabel || "后台任务";
      }
      return monitor;
    } catch (error) {
      const panel = getGlobalPanel();
      if (panel) {
        panel.hidden = false;
        const title = panel.querySelector("[data-task-title]");
        const result = panel.querySelector("[data-task-result]");
        if (title) {
          title.textContent = form.dataset.jobLabel || "后台任务";
        }
        if (result) {
          result.replaceChildren(createElement("p", "task-error", error.message));
        }
      }
    } finally {
      form.dataset.submitting = "false";
      if (submitButton) {
        submitButton.disabled = false;
      }
    }
  }

  async function submitCancellationForm(form, event) {
    event.preventDefault();
    const submitButton = form.querySelector("button");
    if (submitButton) {
      submitButton.disabled = true;
    }
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: {Accept: "application/json"},
      });
      if (!response.ok) {
        throw new Error(`当前任务无法取消（${response.status}）`);
      }
      const payload = await response.json();
      startMonitor(payload.job_id || form.dataset.jobId, {
        statusTarget: document.querySelector("[data-job-monitor]"),
        eventsTarget: document.querySelector("[data-job-events]"),
        resultTarget: document.querySelector("[data-job-result]"),
        useGlobalPanel: false,
      });
    } catch (error) {
      window.alert(error.message);
      if (submitButton) {
        submitButton.disabled = false;
      }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const globalPanel = document.querySelector("[data-task-panel]");
    const restoreJobId = globalPanel?.dataset.taskRestoreJob || "";
    const hasDedicatedJobMonitor =
      document.querySelector("[data-job-monitor]") !== null;
    dbgLog("page-load", {
      globalPanelHidden: globalPanel ? globalPanel.hidden : "no-panel",
      globalPanelJobId:
        globalPanel?.querySelector("form[data-job-cancel]")?.dataset.jobId || "",
      monitorTargets: document.querySelectorAll("[data-job-monitor]").length,
      restoreJobId,
      hasDedicatedJobMonitor,
    });
    if (restoreJobId && !hasDedicatedJobMonitor) {
      startMonitor(restoreJobId, {useGlobalPanel: true});
    }
    document.querySelectorAll("form[data-job-form]").forEach((form) => {
      form.addEventListener("submit", (event) => submitJobForm(form, event));
    });
    document.querySelectorAll("form[data-job-cancel]").forEach((form) => {
      form.addEventListener("submit", (event) => submitCancellationForm(form, event));
    });

    document.querySelectorAll("[data-job-monitor]").forEach((target) => {
      startMonitor(target.dataset.jobId, {
        statusTarget: target,
        eventsTarget: document.querySelector("[data-job-events]"),
        resultTarget: document.querySelector("[data-job-result]"),
        useGlobalPanel: target.dataset.globalTaskPanel !== "false",
      });
    });
  });
})();
