(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var select = document.getElementById("traffic-period");
  var customPeriod = document.getElementById("custom-period");
  var fromInput = document.getElementById("traffic-from");
  var toInput = document.getElementById("traffic-to");
  var applyButton = document.getElementById("traffic-apply");
  var status = document.getElementById("traffic-graph-status");
  var title = document.getElementById("traffic-rate-title");
  var image = document.getElementById("traffic-graph-image");
  var customGraph = document.getElementById("traffic-custom-graph");
  var tableTitle = document.getElementById("traffic-table-title");
  var tableBody = document.getElementById("traffic-period-table-body");
  var tableTotal = document.getElementById("traffic-period-table-total");
  var graphData = null;

  if (!select || !customPeriod || !fromInput || !toInput ||
      !applyButton || !status || !title || !image || !customGraph) {
    return;
  }

  function setStatus(message, isError) {
    status.textContent = message;
    status.className = isError ? "graph-status graph-status-error" : "graph-status";
  }

  function replaceQuery(values) {
    if (!window.history || !window.history.replaceState || !window.URL) {
      return;
    }
    var url = new URL(window.location.href);
    url.searchParams.set("graph", values.graph);
    if (values.graph === "custom") {
      url.searchParams.set("from", values.from);
      url.searchParams.set("to", values.to);
    } else {
      url.searchParams.delete("from");
      url.searchParams.delete("to");
    }
    window.history.replaceState({}, "", url.toString());
  }

  function selectedOption() {
    return select.options[select.selectedIndex];
  }

  function optionBounds(option) {
    var fromEpoch = Number(option.getAttribute("data-from-epoch"));
    var toEpoch = Number(option.getAttribute("data-to-epoch"));
    if (!Number.isFinite(fromEpoch) || !Number.isFinite(toEpoch) ||
        fromEpoch >= toEpoch) {
      return null;
    }
    return {from: fromEpoch, to: toEpoch};
  }

  function staticBounds(option, data) {
    var minutes = Number(option.value);
    var end = Number(data && data.generated);
    if (!Number.isFinite(end) || end <= 0) {
      end = Math.floor(Date.now() / 1000);
    }
    if (!Number.isFinite(minutes) || minutes <= 0) {
      return null;
    }
    return {from: end - minutes * 60, to: end};
  }

  function showStaticPreset(option) {
    var source = option.getAttribute("data-graph-src");
    if (source) {
      image.src = source;
    }
    image.style.display = "block";
    customGraph.style.display = "none";
    customPeriod.style.display = "none";
    title.textContent = "TRAFFIC RATE — " + option.textContent;
    setStatus("", false);
    replaceQuery({graph: select.value});

    loadGraphData()
      .then(function (data) {
        var bounds = staticBounds(option, data);
        if (bounds) {
          renderTrafficTable(
            data.points,
            bounds.from,
            bounds.to,
            option.value,
            option.textContent
          );
        }
      })
      .catch(function (error) {
        renderTableMessage("Unable to load traffic table: " + error.message);
      });
  }

  function svgElement(name, attributes, text) {
    var element = document.createElementNS(SVG_NS, name);
    Object.keys(attributes || {}).forEach(function (key) {
      element.setAttribute(key, attributes[key]);
    });
    if (text !== undefined) {
      element.textContent = text;
    }
    return element;
  }

  function formatRate(value) {
    var units = ["B/s", "KB/s", "MB/s", "GB/s", "TB/s"];
    var size = Math.max(Number(value) || 0, 0);
    var unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit += 1;
    }
    if (unit === 0) {
      return Math.round(size) + " " + units[unit];
    }
    return size.toFixed(1) + " " + units[unit];
  }

  function formatBytes(value) {
    var units = ["B", "KB", "MB", "GB", "TB", "PB"];
    var size = Math.max(Number(value) || 0, 0);
    var unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit += 1;
    }
    if (unit === 0) {
      return Math.round(size) + " " + units[unit];
    }
    return size.toFixed(1) + " " + units[unit];
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function axisTime(epoch, spanSeconds) {
    var value = new Date(epoch * 1000);
    var time = pad(value.getHours()) + ":" + pad(value.getMinutes());
    if (spanSeconds <= 86400) {
      return time;
    }
    if (spanSeconds <= 2678400) {
      return pad(value.getDate()) + "/" + pad(value.getMonth() + 1) + " " + time;
    }
    return pad(value.getDate()) + "/" + pad(value.getMonth() + 1);
  }

  function rangeTitle(label, fromEpoch, toEpoch) {
    return label + " — " +
      new Date(fromEpoch * 1000).toLocaleString() + " — " +
      new Date(toEpoch * 1000).toLocaleString();
  }

  function customTitle(fromEpoch, toEpoch) {
    return rangeTitle("Custom traffic rate", fromEpoch, toEpoch);
  }

  function drawCustom(points, fromEpoch, toEpoch, graphTitle) {
    var width = 900;
    var height = 360;
    var left = 76;
    var top = 38;
    var right = 25;
    var bottom = 62;
    var plotWidth = width - left - right;
    var plotHeight = height - top - bottom;
    var duration = toEpoch - fromEpoch;
    var bucketSeconds = Math.max(
      60,
      Math.ceil(duration / 480 / 60) * 60
    );
    var bucketCount = Math.max(2, Math.ceil(duration / bucketSeconds));
    var minuteSlots = Math.max(bucketSeconds / 60, 1);
    var rx = new Array(bucketCount).fill(0);
    var tx = new Array(bucketCount).fill(0);
    var hasTraffic = false;

    points.forEach(function (point) {
      var epoch = Number(point[0]);
      if (epoch < fromEpoch || epoch > toEpoch) {
        return;
      }
      var index = Math.floor((epoch - fromEpoch) / bucketSeconds);
      index = Math.min(Math.max(index, 0), bucketCount - 1);
      rx[index] += (Number(point[1]) || 0) / minuteSlots;
      tx[index] += (Number(point[2]) || 0) / minuteSlots;
      hasTraffic = hasTraffic || rx[index] > 0 || tx[index] > 0;
    });

    var maximum = Math.max.apply(null, rx.concat(tx).concat([1]));
    var divisor = Math.max(bucketCount - 1, 1);
    function x(index) {
      return left + plotWidth * index / divisor;
    }
    function y(value) {
      return top + plotHeight - value * plotHeight / maximum;
    }

    while (customGraph.firstChild) {
      customGraph.removeChild(customGraph.firstChild);
    }
    customGraph.appendChild(svgElement("title", {}, graphTitle));
    customGraph.appendChild(svgElement("rect", {
      x: 0, y: 0, width: width, height: height, fill: "white"
    }));
    var group = svgElement("g", {
      "font-family": "Tahoma,Verdana,Arial,sans-serif",
      "font-size": "9",
      fill: "#000"
    });
    group.appendChild(svgElement("text", {
      x: left, y: 17, "font-weight": "bold"
    }, graphTitle));

    for (var step = 0; step <= 5; step += 1) {
      var gridY = top + plotHeight * step / 5;
      var gridValue = maximum * (5 - step) / 5;
      group.appendChild(svgElement("line", {
        x1: left, y1: gridY, x2: width - right, y2: gridY,
        stroke: "#d8d8d8"
      }));
      group.appendChild(svgElement("text", {
        x: left - 8, y: gridY + 3, "text-anchor": "end"
      }, formatRate(gridValue)));
    }
    group.appendChild(svgElement("line", {
      x1: left, y1: top, x2: left, y2: top + plotHeight, stroke: "#333"
    }));
    group.appendChild(svgElement("line", {
      x1: left, y1: top + plotHeight, x2: width - right,
      y2: top + plotHeight, stroke: "#333"
    }));

    group.appendChild(svgElement("polyline", {
      points: rx.map(function (value, index) {
        return x(index).toFixed(1) + "," + y(value).toFixed(1);
      }).join(" "),
      fill: "none", stroke: "#436EEE", "stroke-width": "2"
    }));
    group.appendChild(svgElement("polyline", {
      points: tx.map(function (value, index) {
        return x(index).toFixed(1) + "," + y(value).toFixed(1);
      }).join(" "),
      fill: "none", stroke: "#FF8C00", "stroke-width": "2"
    }));

    for (var label = 0; label <= 6; label += 1) {
      var labelIndex = Math.round((bucketCount - 1) * label / 6);
      group.appendChild(svgElement("text", {
        x: x(labelIndex), y: height - 35, "text-anchor": "middle"
      }, axisTime(
        fromEpoch + labelIndex * bucketSeconds,
        duration
      )));
    }
    group.appendChild(svgElement("rect", {
      x: width - 150, y: 10, width: 10, height: 10, fill: "#436EEE"
    }));
    group.appendChild(svgElement("text", {
      x: width - 135, y: 19
    }, "RX/s"));
    group.appendChild(svgElement("rect", {
      x: width - 85, y: 10, width: 10, height: 10, fill: "#FF8C00"
    }));
    group.appendChild(svgElement("text", {
      x: width - 70, y: 19
    }, "TX/s"));
    if (!hasTraffic) {
      group.appendChild(svgElement("text", {
        x: left + plotWidth / 2,
        y: top + plotHeight / 2,
        "text-anchor": "middle",
        fill: "#666"
      }, "No traffic in selected period"));
    }
    customGraph.appendChild(group);
    image.style.display = "none";
    customGraph.style.display = "block";
  }

  function loadGraphData() {
    if (graphData) {
      return Promise.resolve(graphData);
    }
    return fetch("traffic-history.json", {cache: "no-store"})
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (data) {
        if ((data.version !== 1 && data.version !== 2) || !Array.isArray(data.points)) {
          throw new Error("unsupported graph data");
        }
        graphData = data;
        return data;
      });
  }

  function pointBytes(point) {
    var rxBytes = Number(point[3]);
    var txBytes = Number(point[4]);
    if (Number.isFinite(rxBytes) && Number.isFinite(txBytes)) {
      return {
        rx: Math.max(rxBytes, 0),
        tx: Math.max(txBytes, 0)
      };
    }
    return {
      rx: Math.max((Number(point[1]) || 0) * 60, 0),
      tx: Math.max((Number(point[2]) || 0) * 60, 0)
    };
  }

  function tableBucketSeconds(periodValue, duration) {
    if (periodValue === "60") {
      return 5 * 60;
    }
    if (periodValue === "10080" || periodValue === "43200" ||
        periodValue === "previous-month") {
      return 24 * 60 * 60;
    }
    if (periodValue === "custom") {
      return duration <= 24 * 60 * 60 ? 60 * 60 : 24 * 60 * 60;
    }
    return 60 * 60;
  }

  function formatDateTime(epoch) {
    var value = new Date(epoch * 1000);
    return pad(value.getDate()) + "/" + pad(value.getMonth() + 1) + "/" +
      value.getFullYear() + " " + pad(value.getHours()) + ":" + pad(value.getMinutes());
  }

  function formatDay(epoch) {
    var value = new Date(epoch * 1000);
    return pad(value.getDate()) + "/" + pad(value.getMonth() + 1) + "/" + value.getFullYear();
  }

  function tablePeriodLabel(start, end, bucketSeconds) {
    if (bucketSeconds >= 24 * 60 * 60) {
      return formatDay(start);
    }
    return formatDateTime(start) + " — " + formatDateTime(end);
  }

  function clearNode(node) {
    while (node && node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function addCell(row, text, className, tagName) {
    var cell = document.createElement(tagName || "td");
    cell.className = className || "data";
    cell.textContent = text;
    row.appendChild(cell);
  }

  function renderTableMessage(message) {
    if (!tableBody) {
      return;
    }
    clearNode(tableBody);
    var row = document.createElement("tr");
    var cell = document.createElement("td");
    cell.className = "data3";
    cell.colSpan = 4;
    cell.textContent = message;
    row.appendChild(cell);
    tableBody.appendChild(row);
    clearNode(tableTotal);
  }

  function renderTrafficTable(points, fromEpoch, toEpoch, periodValue, label) {
    if (!tableBody || !Number.isFinite(fromEpoch) || !Number.isFinite(toEpoch) ||
        fromEpoch >= toEpoch) {
      return;
    }

    var duration = toEpoch - fromEpoch;
    var bucketSeconds = tableBucketSeconds(periodValue, duration);
    var bucketCount = Math.max(1, Math.ceil(duration / bucketSeconds));
    var buckets = new Array(bucketCount).fill(null).map(function () {
      return {rx: 0, tx: 0};
    });

    points.forEach(function (point) {
      var epoch = Number(point[0]);
      if (!Number.isFinite(epoch) || epoch < fromEpoch || epoch > toEpoch) {
        return;
      }
      var index = Math.floor((epoch - fromEpoch) / bucketSeconds);
      index = Math.min(Math.max(index, 0), bucketCount - 1);
      var bytes = pointBytes(point);
      buckets[index].rx += bytes.rx;
      buckets[index].tx += bytes.tx;
    });

    clearNode(tableBody);
    var totalRx = 0;
    var totalTx = 0;
    buckets.forEach(function (bucket, index) {
      var start = fromEpoch + index * bucketSeconds;
      var end = Math.min(start + bucketSeconds, toEpoch);
      totalRx += bucket.rx;
      totalTx += bucket.tx;
      var row = document.createElement("tr");
      addCell(row, tablePeriodLabel(start, end, bucketSeconds), "data2");
      addCell(row, formatBytes(bucket.rx), "data");
      addCell(row, formatBytes(bucket.tx), "data");
      addCell(row, formatBytes(bucket.rx + bucket.tx), "data");
      tableBody.appendChild(row);
    });

    if (tableTitle) {
      tableTitle.textContent = "TRAFFIC VOLUME — " + label;
    }
    if (tableTotal) {
      clearNode(tableTotal);
      var totalRow = document.createElement("tr");
      addCell(totalRow, "TOTAL", "header_l", "th");
      addCell(totalRow, formatBytes(totalRx), "header_r", "th");
      addCell(totalRow, formatBytes(totalTx), "header_r", "th");
      addCell(totalRow, formatBytes(totalRx + totalTx), "header_r", "th");
      tableTotal.appendChild(totalRow);
    }
  }

  function showDynamicPreset(option) {
    var bounds = optionBounds(option);
    if (!bounds) {
      showStaticPreset(option);
      return;
    }
    customPeriod.style.display = "none";
    setStatus("Loading history…", false);
    loadGraphData()
      .then(function (data) {
        var label = option.textContent;
        drawCustom(
          data.points,
          bounds.from,
          bounds.to,
          rangeTitle(label, bounds.from, bounds.to)
        );
        renderTrafficTable(
          data.points,
          bounds.from,
          bounds.to,
          option.value,
          label
        );
        title.textContent = "TRAFFIC RATE — " + label;
        setStatus(
          new Date(bounds.from * 1000).toLocaleString() +
          " — " + new Date(bounds.to * 1000).toLocaleString(),
          false
        );
        replaceQuery({graph: select.value});
      })
      .catch(function (error) {
        image.style.display = "block";
        customGraph.style.display = "none";
        renderTableMessage("Unable to load traffic table: " + error.message);
        setStatus("Unable to load graph history: " + error.message, true);
      });
  }

  function applyCustom() {
    var fromDate = new Date(fromInput.value);
    var toDate = new Date(toInput.value);
    var fromEpoch = Math.floor(fromDate.getTime() / 1000);
    var toEpoch = Math.floor(toDate.getTime() / 1000);
    if (!Number.isFinite(fromEpoch) || !Number.isFinite(toEpoch) ||
        fromEpoch >= toEpoch) {
      setStatus("FROM must be earlier than TO", true);
      renderTableMessage("FROM must be earlier than TO");
      return;
    }
    setStatus("Loading history…", false);
    loadGraphData()
      .then(function (data) {
        drawCustom(data.points, fromEpoch, toEpoch, customTitle(fromEpoch, toEpoch));
        renderTrafficTable(
          data.points,
          fromEpoch,
          toEpoch,
          "custom",
          "CUSTOM DATE / TIME"
        );
        title.textContent = "TRAFFIC RATE — CUSTOM DATE / TIME";
        setStatus(
          "CUSTOM · " + new Date(fromEpoch * 1000).toLocaleString() +
          " — " + new Date(toEpoch * 1000).toLocaleString(),
          false
        );
        replaceQuery({
          graph: "custom",
          from: fromInput.value,
          to: toInput.value
        });
      })
      .catch(function (error) {
        image.style.display = "block";
        customGraph.style.display = "none";
        renderTableMessage("Unable to load traffic table: " + error.message);
        setStatus("Unable to load graph history: " + error.message, true);
      });
  }

  function choosePeriod() {
    var option = selectedOption();
    if (select.value === "custom") {
      customPeriod.style.display = "inline-block";
      applyCustom();
    } else if (option.getAttribute("data-graph-src")) {
      showStaticPreset(option);
    } else {
      showDynamicPreset(option);
    }
  }

  select.addEventListener("change", choosePeriod);
  applyButton.addEventListener("click", applyCustom);

  var params = window.URLSearchParams ?
    new URLSearchParams(window.location.search) : null;
  var requested = params ? params.get("graph") : null;
  if (requested) {
    for (var optionIndex = 0; optionIndex < select.options.length; optionIndex += 1) {
      if (select.options[optionIndex].value === requested) {
        select.value = requested;
        break;
      }
    }
  }
  if (select.value === "custom" && params) {
    if (params.get("from")) {
      fromInput.value = params.get("from");
    }
    if (params.get("to")) {
      toInput.value = params.get("to");
    }
  }
  choosePeriod();
}());
