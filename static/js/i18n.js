/* Arabic interface translation for learner pages.
 * Walks visible text and replaces exact interface strings with Arabic from the
 * server-provided dictionary. Authored course content is already served in
 * Arabic from the database, so this only affects fixed UI labels/buttons.
 * Runs only on right-to-left (Arabic) pages. */
(function () {
    var dict = window.__AR__ || {};
    if (!Object.keys(dict).length) return;

    // Don't translate inside editable fields, code, or script/style.
    var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CODE: 1, PRE: 1 };

    function translateText(root) {
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode: function (node) {
                if (node.parentNode && SKIP[node.parentNode.nodeName]) return NodeFilter.FILTER_REJECT;
                return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
            }
        });
        var nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        nodes.forEach(function (n) {
            var raw = n.nodeValue;
            var key = raw.trim();
            if (dict[key]) {
                n.nodeValue = raw.replace(key, dict[key]);
                return;
            }
            // Handle required-field labels like "Industry *"
            var m = key.match(/^(.*?)(\s*\*)$/);
            if (m && dict[m[1].trim()]) {
                n.nodeValue = raw.replace(key, dict[m[1].trim()] + ' *');
            }
        });
    }

    function translateAttrs(root) {
        root.querySelectorAll('[placeholder]').forEach(function (el) {
            var k = (el.getAttribute('placeholder') || '').trim();
            if (dict[k]) el.setAttribute('placeholder', dict[k]);
        });
        root.querySelectorAll('input[type=submit], input[type=button]').forEach(function (el) {
            var k = (el.value || '').trim();
            if (dict[k]) el.value = dict[k];
        });
        root.querySelectorAll('[title]').forEach(function (el) {
            var k = (el.getAttribute('title') || '').trim();
            if (dict[k]) el.setAttribute('title', dict[k]);
        });
    }

    function run() {
        translateText(document.body);
        translateAttrs(document.body);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', run);
    } else {
        run();
    }
})();
