(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  // --- Chart 1: 提及量趋势 ---
  var chart1 = echarts.init(document.getElementById('chart-mention-trend'), null, { renderer: 'svg' });
  chart1.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      backgroundColor: '#fff',
      borderColor: rule,
      textStyle: { color: ink, fontSize: 13 }
    },
    grid: { left: 50, right: 30, top: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: ['7/29', '8/5', '8/10', '8/14', '8/15', '8/19'],
      axisLine: { lineStyle: { color: rule } },
      axisTick: { show: false },
      axisLabel: { color: muted, fontSize: 12 },
      name: '日期（2026年）',
      nameTextStyle: { color: muted, fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      name: '报道/讨论数',
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: rule, type: 'dashed' } },
      axisLabel: { color: muted, fontSize: 12 },
      nameTextStyle: { color: muted, fontSize: 11 }
    },
    series: [{
      type: 'line',
      data: [12, 18, 6, 14, 10, 5],
      smooth: true,
      symbol: 'circle',
      symbolSize: 8,
      lineStyle: { color: accent, width: 3 },
      itemStyle: { color: accent },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
        { offset: 0, color: accent + '33' },
        { offset: 1, color: accent + '05' }
      ]}},
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: accent2, type: 'dashed', width: 1 },
        label: { color: accent2, fontSize: 11, formatter: '首销周' },
        data: [{ xAxis: '8/14' }]
      }
    }]
  });
  window.addEventListener('resize', function() { chart1.resize(); });

  // --- Chart 2: 情绪分布 ---
  var chart2 = echarts.init(document.getElementById('chart-sentiment'), null, { renderer: 'svg' });
  chart2.setOption({
    animation: false,
    tooltip: {
      trigger: 'item',
      appendToBody: true,
      backgroundColor: '#fff',
      borderColor: rule,
      textStyle: { color: ink, fontSize: 13 },
      formatter: '{b}: {c} 条 ({d}%)'
    },
    legend: {
      bottom: 0,
      textStyle: { color: muted, fontSize: 12 },
      itemWidth: 12,
      itemHeight: 12
    },
    series: [{
      type: 'pie',
      radius: ['55%', '78%'],
      center: ['50%', '48%'],
      avoidLabelOverlap: false,
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
      label: {
        show: true,
        position: 'outside',
        formatter: '{b}\n{d}%',
        color: ink,
        fontSize: 13
      },
      emphasis: { scale: false },
      data: [
        { value: 11, name: '正面', itemStyle: { color: '#22C55E' } },
        { value: 5, name: '中性', itemStyle: { color: accent2 } },
        { value: 4, name: '负面', itemStyle: { color: accent } }
      ]
    }]
  });
  window.addEventListener('resize', function() { chart2.resize(); });

  // --- Chart 3: 风险等级评估 ---
  var chart3 = echarts.init(document.getElementById('chart-risk'), null, { renderer: 'svg' });
  var riskColors = {
    '高': '#EF4444',
    '中': '#F59E0B',
    '低': '#22C55E'
  };
  var riskData = [
    { name: '网银/企业安全工具不兼容', value: 9, level: '高' },
    { name: '专业软件生态缺失', value: 9, level: '高' },
    { name: '仅限中国市场发售', value: 7, level: '中' },
    { name: '仅2个USB-C接口', value: 6, level: '中' },
    { name: '"手机芯片电脑"标签化', value: 6, level: '中' },
    { name: '社区讨论冷感(HN/V2EX)', value: 5, level: '中' },
    { name: '存储不可扩展', value: 3, level: '低' },
    { name: '价格门槛较高', value: 3, level: '低' },
    { name: '鸿蒙品牌认知分化', value: 2, level: '低' }
  ];
  chart3.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      backgroundColor: '#fff',
      borderColor: rule,
      textStyle: { color: ink, fontSize: 13 },
      formatter: function(p) {
        var d = p[0];
        return d.name + '<br/>风险评分: ' + d.value + '/10' + '<br/>等级: ' + riskData[d.dataIndex].level;
      }
    },
    grid: { left: 180, right: 60, top: 20, bottom: 30 },
    xAxis: {
      type: 'value',
      max: 10,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: rule, type: 'dashed' } },
      axisLabel: { color: muted, fontSize: 11, formatter: '{value}/10' }
    },
    yAxis: {
      type: 'category',
      data: riskData.map(function(d) { return d.name; }),
      inverse: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: ink, fontSize: 12, width: 170, overflow: 'truncate' }
    },
    series: [{
      type: 'bar',
      data: riskData.map(function(d) {
        return {
          value: d.value,
          itemStyle: { color: riskColors[d.level], borderRadius: [0, 4, 4, 0] }
        };
      }),
      barWidth: 20,
      label: {
        show: true,
        position: 'right',
        color: muted,
        fontSize: 11,
        formatter: function(p) { return riskData[p.dataIndex].level; }
      }
    }]
  });
  window.addEventListener('resize', function() { chart3.resize(); });
})();