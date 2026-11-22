<?php
// PHP-only scanner. The existing Python and BAT translation workflow is unchanged.
function translation_cache_step($cursor = 0, $force = false) {
    $base = 'e:/Carte/BB/17 - Site Leadership/';
    $ro = $base . 'Principal/ro/'; $en = $base . 'Principal/en/';
    $out = $base . 'alte/Ionel Balauta/Aryeht/Task 1 - Traduce tot site-ul/Doar Google Web/Andreea/Meditatii/2023/Iulia Python/';
    $lock = fopen($out . 'fisiere_traduse.lock', 'c');
    if (!$lock || !flock($lock, LOCK_EX)) throw new Exception('Nu pot bloca registrul traducerilor.');
    try {
        $cacheFile = $out . 'fisiere_traduse.cache.json';
        $cache = is_file($cacheFile) ? json_decode(file_get_contents($cacheFile), true) : [];
        if (!is_array($cache)) $cache = [];
        $entries = $cache['entries'] ?? [];
        $names = array_values(array_filter(scandir($ro), function($n) use ($ro) { return substr($n, -5) === '.html' && is_file($ro . $n); }));
        sort($names); $total = count($names);
        $full = $force || time() - ($cache['full_checked'] ?? 0) >= 604800;
        $signature = function($path) { clearstatcache(true, $path); return is_file($path) ? [filemtime($path), filesize($path)] : null; };
        $limit = min($cursor + 60, $total);
        $skipped = 0;
        for ($i = $cursor; $i < $limit; $i++) {
            $name = $names[$i]; $sig = $signature($ro . $name); $old = $entries[$name] ?? null;
            if (!$full && $old && !empty($old['translated']) && $old['ro'] === $sig && $old['en_sig'] === $signature($en . $old['en'])) { $skipped++; continue; }
            $html = file_get_contents($ro . $name);
            if ($html === false) throw new Exception('Nu pot citi ' . $name);
            $article = strpos($html, '<!-- ARTICOL START -->') !== false && strpos($html, 'class="den_articol"') !== false && strpos($html, '<!-- SASA-1 -->') !== false;
            $entry = ['ro' => $sig, 'article' => $article, 'translated' => false, 'en' => '', 'en_sig' => null];
            if ($article) {
                preg_match('/<!-- FLAGS_1 -->.*?<!-- FLAGS -->/s', $html, $flags);
                if (preg_match('~neculaifantanaru\.com/en/([^"\r\n]+\.html)~', $flags[0] ?? '', $m) && !preg_match('~[\\\\/]~', $m[1])) {
                    $entry['en'] = $m[1]; $entry['en_sig'] = $signature($en . $m[1]);
                    if ($entry['en_sig']) {
                        $english = file_get_contents($en . $m[1]);
                        if ($english === false) throw new Exception('Nu pot citi traducerea ' . $m[1]);
                        preg_match('/<!-- FLAGS_1 -->.*?<!-- FLAGS -->/s', $english, $ef);
                        $back = preg_match('~neculaifantanaru\.com/([^"/]+\.html)~', $ef[0] ?? '', $r);
                        // Same eligibility rule as gaseste_netraduse() in the original Python.
                        $entry['translated'] = !$back || $r[1] === $name;
                    }
                }
            }
            $entries[$name] = $entry;
        }
        $entries = array_intersect_key($entries, array_flip($names));
        $done = $limit >= $total; $missing = []; $translated = [];
        foreach ($names as $name) {
            $e = $entries[$name] ?? [];
            if (!empty($e['translated'])) $translated[] = $name;
            elseif (!empty($e['article'])) $missing[] = $name;
        }
        $cache['entries'] = $entries;
        if ($done && $full) $cache['full_checked'] = time();
        $json = json_encode($cache, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR);
        if (file_put_contents($cacheFile . '.tmp', $json) === false || !rename($cacheFile . '.tmp', $cacheFile)) throw new Exception('Nu pot salva cache-ul.');
        if ($done) {
            $txt = implode("\r\n", $translated) . "\r\n";
            $path = $out . 'fisiere_traduse.txt';
            if (file_put_contents($path . '.tmp', $txt) === false || !rename($path . '.tmp', $path)) throw new Exception('Nu pot salva fisiere_traduse.txt.');
        }
        return ['ok' => true, 'done' => $done, 'next' => $limit, 'total' => $total, 'skipped' => $skipped, 'translated' => count($translated), 'files' => $done ? $missing : []];
    } finally { flock($lock, LOCK_UN); fclose($lock); }
}
if (PHP_SAPI === 'cli' && realpath($_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    try {
        $cursor = 0;
        do { $r = translation_cache_step($cursor, in_array('--full', $argv, true)); $cursor = $r['next']; echo json_encode($r, JSON_UNESCAPED_UNICODE) . PHP_EOL; } while (!$r['done']);
    } catch (Throwable $e) { fwrite(STDERR, $e->getMessage() . PHP_EOL); exit(1); }
}
