(() => {
  "use strict";

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
    creator_enrich: "补齐达人资料",
    report_export: "报表导出",
    content_thanks_preview: "预演已完成感谢私信",
    operator_prepare: "打开店铺",
    operator_screen: "只出名单",
    operator_pipeline: "筛查批准写飞书发私信",
    operator_tracking: "获取物流信息写飞书发单号",
    auto_approval_preview: "自动审批 · 只读筛查",
    auto_approval_execute: "自动审批 · 执行批准",
    auto_approval_reconcile: "自动审批 · 补写核对",
    auto_approval_order_backfill: "自动审批 · 补写订单号",
  };

  const operatorJobTypes = new Set([
    "operator_prepare",
    "operator_screen",
    "operator_pipeline",
    "operator_tracking",
  ]);

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

  function requestTypedConfirmation({title, description, token, actionLabel}) {
    const dialog = document.querySelector("[data-confirm-dialog]");
    const dialogForm = dialog?.querySelector("[data-confirm-dialog-form]");
    const titleTarget = dialog?.querySelector("[data-confirm-dialog-title]");
    const descriptionTarget = dialog?.querySelector("[data-confirm-dialog-description]");
    const tokenTarget = dialog?.querySelector("[data-confirm-dialog-token]");
    const input = dialog?.querySelector("[data-confirm-dialog-input]");
    const errorTarget = dialog?.querySelector("[data-confirm-dialog-error]");
    const actionButton = dialog?.querySelector("[data-confirm-dialog-action]");

    if (!dialog || !dialogForm || !input || typeof dialog.showModal !== "function") {
      const typedValue = window.prompt(`${title}\n\n${description}\n\n请输入 ${token} 继续：`);
      return Promise.resolve(typedValue?.trim() === token ? token : null);
    }

    titleTarget.textContent = title;
    descriptionTarget.textContent = description;
    tokenTarget.textContent = token;
    actionButton.textContent = actionLabel;
    input.value = "";
    errorTarget.hidden = true;

    return new Promise((resolve) => {
      function finish(value) {
        dialogForm.removeEventListener("submit", handleSubmit);
        dialog.removeEventListener("cancel", handleCancel);
        if (dialog.open) {
          dialog.close(value ? "confirm" : "cancel");
        }
        resolve(value);
      }

      function handleCancel(event) {
        event.preventDefault();
        finish(null);
      }

      function handleSubmit(event) {
        event.preventDefault();
        if (event.submitter?.value === "cancel") {
          finish(null);
          return;
        }
        if (input.value.trim() !== token) {
          errorTarget.hidden = false;
          input.focus();
          return;
        }
        finish(token);
      }

      dialogForm.addEventListener("submit", handleSubmit);
      dialog.addEventListener("cancel", handleCancel);
      dialog.showModal();
      input.focus();
    });
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

    if (job.job_type === "content_thanks_preview") {
      return [
        ["候选达人", `${formatNumber(result.candidates)} 位`],
        ["已发感谢", `${formatNumber(result.already_sent)} 位`],
        ["待发送", `${formatNumber(result.preview)} 位`],
        ["需人工确认", `${formatNumber(result.hold)} 位`],
        ["暂不需要发送", `${formatNumber(result.skip)} 位`],
        ["实际执行", result.execute ? "已下发" : "未执行（预演）"],
        ["写回飞书", result.write_feishu ? "已写入" : "未写入（预演）"],
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

    if (job.job_type === "creator_enrich") {
      const resultEntries = [
        ["待补齐", `${formatNumber(result.missing)} 位达人`],
        ["类型补齐", `${formatNumber(result.type_filled)} 位`],
        ["语言补齐", `${formatNumber(result.language_filled)} 位`],
        ["补齐失败", `${formatNumber(result.failed)} 位`],
      ];
      if (result.unprocessed > 0) {
        resultEntries.push(["未处理", `${formatNumber(result.unprocessed)} 项`]);
      }
      return resultEntries;
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

    if (operatorJobTypes.has(job.job_type)) {
      const resultEntries = [["结果", result.summary || "操作已完成"]];
      if (result.report_name) {
        resultEntries.push(["报表", `exports/${result.report_name}`]);
      }
      if (result.force_used) {
        resultEntries.push(["时间门", "已明确输入 FORCE 并绕过"]);
      }
      return resultEntries;
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

    if (job.status === "cancelled") {
      // 检查点取消不是失败：已经处理的那些行是真的处理完了，要如实显示。
      const cancelledResult = parseResultSummary(job);
      const cancelledEntries = getResultEntries(job, cancelledResult);
      if (cancelledEntries.length > 0) {
        const cancelledList = createElement("dl", "result-list");
        for (const [label, value] of cancelledEntries) {
          appendMetadataRow(cancelledList, label, value);
        }
        target.append(cancelledList);
      } else {
        target.append(
          createElement("p", "task-error", "任务已在安全检查点取消。"),
        );
      }
      if (job.log_path) {
        target.append(createElement("small", "text-secondary", `任务日志：${job.log_path}`));
      }
      return;
    }

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
      if (job.log_path) {
        target.append(createElement("small", "text-secondary", `任务日志：${job.log_path}`));
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

  function bindClearOutputButton() {
    const panel = getGlobalPanel();
    const clearButton = panel?.querySelector("[data-task-clear-output]");
    if (!panel || !clearButton || clearButton.dataset.bound) {
      return;
    }
    clearButton.dataset.bound = "true";
    clearButton.addEventListener("click", () => {
      // 仅 UI 层清空：清掉执行过程列表，不动运行结果（数据还在后端）。
      const events = panel.querySelector("[data-task-events]");
      if (events) {
        events.replaceChildren();
      }
      const progressMessage = panel.querySelector("[data-task-progress-message]");
      if (progressMessage) {
        progressMessage.textContent = "输出已清空";
      }
    });
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
    const clearOutputButton = panel.querySelector("[data-task-clear-output]");
    panel.classList.toggle("task-panel--warning", monitor.hasWarning);

    bindClearOutputButton();
    if (clearOutputButton) {
      clearOutputButton.hidden = false;
    }

    if (title) {
      title.textContent = getJobTypeLabel(job.job_type);
    }
    const eyebrow = panel.querySelector("[data-task-eyebrow]");
    if (eyebrow) {
      eyebrow.textContent = terminalStatuses.has(job.status) ? "最近任务" : "正在处理";
    }
    if (status) {
      status.replaceChildren(createStatusToken(job.status));
    }
    if (cancelForm && cancelButton) {
      // 服务端给结论：只有登记了安全检查点的任务才显示取消
      // （写任务里物流任务可以在整行边界停下，平台批准 / 私信不在列）。
      const cancellable = job.can_cancel === true;
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
      // 轮询快照可能早于 SSE 发完尾部事件；先补齐全量事件再关闭连接，
      // 避免事件日志停在任务中段。
      if (!monitor.terminalFinalized) {
        monitor.terminalFinalized = true;
        finalizeMonitor(monitor, job.id);
      }
      document.dispatchEvent(new CustomEvent("assistant:job-complete", {detail: job}));
    }
  }

  function monitorEventTarget(monitor) {
    return monitor.eventsTarget || (monitor.useGlobalPanel
      ? getGlobalPanel()?.querySelector("[data-task-events]")
      : null);
  }

  async function finalizeMonitor(monitor, jobId) {
    try {
      const response = await fetch(
        `/api/jobs/${encodeURIComponent(jobId)}/events`,
        {credentials: "same-origin", headers: {Accept: "application/json"}},
      );
      if (response.ok) {
        const payload = await response.json();
        for (const eventPayload of payload.events || []) {
          appendEvent(monitorEventTarget(monitor), eventPayload, monitor);
        }
      }
    } catch (_error) {
      // SSE 已尽力补发；一次性补齐失败不阻塞收尾。
    } finally {
      stopMonitor(monitor);
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
    const clearOutputButton = panel.querySelector("[data-task-clear-output]");
    if (clearOutputButton) {
      clearOutputButton.hidden = true;
    }
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
    if (monitor.useGlobalPanel) {
      const cancelForm = getGlobalPanel()?.querySelector("form[data-job-cancel]");
      const cancelButton = cancelForm?.querySelector("button");
      if (cancelForm) {
        cancelForm.action = `/api/jobs/${encodeURIComponent(jobId)}/cancel`;
        cancelForm.dataset.jobId = jobId;
      }
      if (cancelButton) {
        cancelButton.hidden = false;
        cancelButton.disabled = false;
      }
    }

    const eventTarget = monitorEventTarget(monitor);

    const refreshSnapshot = () => {
      fetchJob(jobId)
        .then((job) => {
          handleJobSnapshot(monitor, job);
        })
        .catch((error) => {
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
      const requestBody = new FormData(form);
      const confirmationToken = form.dataset.confirmToken || "";
      if (confirmationToken) {
        const confirmedValue = await requestTypedConfirmation({
          title: form.dataset.confirmTitle || "确认执行",
          description: form.dataset.confirmDescription || "此操作可能无法撤销。",
          token: confirmationToken,
          actionLabel: form.dataset.confirmAction || "确认并开始",
        });
        if (!confirmedValue) {
          return;
        }
        requestBody.set("confirmation", confirmedValue);
      }

      let response = await fetch(form.action, {
        method: (form.method || "POST").toUpperCase(),
        body: requestBody,
        credentials: "same-origin",
        headers: {Accept: "application/json"},
      });
      if (response.status === 409 && form.dataset.forceGate === "tracking") {
        const forcePayload = await response.json();
        if (forcePayload.detail?.code === "force-required") {
          const forceValue = await requestTypedConfirmation({
            title: "强制在 16:00 前运行",
            description: `现在北京时间 ${forcePayload.detail.clock}。强制运行只会绕过时间门，仍会写飞书并发送不可撤回的物流私信。`,
            token: "FORCE",
            actionLabel: "强制运行",
          });
          if (!forceValue) {
            return;
          }
          requestBody.set("force_confirmation", forceValue);
          response = await fetch(form.action, {
            method: (form.method || "POST").toUpperCase(),
            body: requestBody,
            credentials: "same-origin",
            headers: {Accept: "application/json"},
          });
        }
      }
      if (!response.ok) {
        let errorMessage = `无法开始任务（${response.status}）`;
        try {
          const errorPayload = await response.json();
          if (typeof errorPayload.detail === "string") {
            errorMessage = errorPayload.detail;
          } else if (errorPayload.detail?.message) {
            errorMessage = errorPayload.detail.message;
          }
        } catch (_error) {
          /* Keep the status-based fallback for non-JSON failures. */
        }
        throw new Error(errorMessage);
      }
      const payload = await response.json();
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
      const jobId = payload.job_id || form.dataset.jobId;
      const existingMonitor = activeMonitors.get(jobId);
      if (existingMonitor) {
        fetchJob(jobId)
          .then((job) => handleJobSnapshot(existingMonitor, job))
          .catch((error) => {
            showMonitorError(existingMonitor, error);
            if (submitButton) {
              submitButton.disabled = false;
            }
          });
        return;
      }
      const dedicatedTarget = document.querySelector("[data-job-monitor]");
      startMonitor(jobId, {
        statusTarget: dedicatedTarget,
        eventsTarget: document.querySelector("[data-job-events]"),
        resultTarget: document.querySelector("[data-job-result]"),
        useGlobalPanel: dedicatedTarget
          ? dedicatedTarget.dataset.globalTaskPanel !== "false"
          : true,
      });
    } catch (error) {
      window.alert(error.message);
      if (submitButton) {
        submitButton.disabled = false;
      }
    }
  }

  function startJobMonitorForTarget(target) {
    if (!target || target.dataset.monitorStarted === "true") {
      return;
    }
    target.dataset.monitorStarted = "true";
    startMonitor(target.dataset.jobId, {
      statusTarget: target,
      eventsTarget: document.querySelector("[data-job-events]"),
      resultTarget: document.querySelector("[data-job-result]"),
      useGlobalPanel: target.dataset.globalTaskPanel !== "false",
    });
  }

  // 表单走事件委托：React 页面在 DOMContentLoaded 之后才挂载，
  // 逐节点绑定会漏掉这些表单。
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (form.matches("form[data-job-form]")) {
      submitJobForm(form, event);
      return;
    }
    if (form.matches("form[data-job-cancel]")) {
      submitCancellationForm(form, event);
    }
  });

  // React 挂载后主动通知：任务详情页带 data-job-monitor；自动批准页只传 jobId，
  // 走全局面板。DOMContentLoaded 时 React 可能还没挂上。
  document.addEventListener("assistant:monitor-job", (event) => {
    const dedicatedTarget = document.querySelector("[data-job-monitor]");
    if (dedicatedTarget) {
      startJobMonitorForTarget(dedicatedTarget);
      return;
    }
    const jobId = event.detail && event.detail.jobId;
    if (jobId) {
      startMonitor(jobId, {useGlobalPanel: true});
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    const globalPanel = document.querySelector("[data-task-panel]");
    const restoreJobId = globalPanel?.dataset.taskRestoreJob || "";
    const hasDedicatedJobMonitor =
      document.querySelector("[data-job-monitor]") !== null;
    if (restoreJobId && !hasDedicatedJobMonitor) {
      startMonitor(restoreJobId, {useGlobalPanel: true});
    }
    document.querySelectorAll("[data-job-monitor]").forEach(startJobMonitorForTarget);
  });
})();
